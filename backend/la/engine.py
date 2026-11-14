"""Vectorized LA hindcast engine. Deterministic, numpy only, no LLM.

For a list of UTC hours it computes, for every branch and tower point:
  DC power flow (PTDF per island)  ->  current  ->  IEEE 738 conductor temperature (thermal.py)
  ->  thermal sag  ->  wind blowout (gust perpendicular to the conductor)  ->  vegetation clearance
  ->  fire risk index (documented formula, calibrated thresholds)
Weather comes from the Open-Meteo grid, bilinearly interpolated to each tower point, with the
station-based gust correction from calibration.json.

Fire risk per tower-hour (all factors 0..1):
  f_wind  = sigmoid((gust_corrected - g50) / 2 m/s)
  f_dry   = sigmoid((h50 - RH) / 5 %)
  f_clear = sigmoid((c50 - clearance) / 1 m)
  f_fuel  = fuel weight by terrain zone (mountain 1.0, valley 0.7, coast 0.4, basin 0.35; 0 for cables)
  risk    = f_wind * f_dry * (0.5 + 0.5 f_clear) * f_fuel
g50, h50, c50 are fit on training events only (validation/calibrate.py). A line's risk is its worst tower.
Region risk index RI(t) = sum over tower points of risk * span_km  ("risk-weighted line-km").
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import thermal as TH
from . import data as D

ZONES = ["coast", "basin", "valley", "mountain"]
FUEL = {"coast": 0.4, "basin": 0.35, "valley": 0.7, "mountain": 1.0}
LOCAL_GEN_FRACTION = 0.45  # share of regional load met by in-basin plants; rest imported (MODELED)


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def damage_prob(gust, clr, onset, c50):
    """P(fault) per tower span: wind load on the conductor and nearby vegetation (calibrated on outages, storm.py)."""
    return sigmoid((gust - onset) / 2.0) * (0.5 + 0.5 * sigmoid((c50 - clr) / 1.0))


# LANDFIRE FBFM40 fuel model -> receptivity of the fine fuel to a spark (0-1). Judgment values, NOT fitted:
# grass catches first, then grass-shrub, slash, shrub, timber understory, timber litter; NB = non-burnable (urban,
# water, barren, agriculture) keeps a small floor for landscaping/debris. See validation/DECISIONS.md.
def fuel_receptivity(fbfm):
    f = np.asarray(fbfm)
    out = np.full(f.shape, 0.05)
    out[(f >= 101) & (f <= 109)] = 1.0   # GR grass
    out[(f >= 121) & (f <= 124)] = 0.9   # GS grass-shrub
    out[(f >= 201) & (f <= 204)] = 0.7   # SB slash-blowdown
    out[(f >= 141) & (f <= 149)] = 0.8   # SH shrub
    out[(f >= 161) & (f <= 165)] = 0.6   # TU timber-understory
    out[(f >= 181) & (f <= 189)] = 0.4   # TL timber litter
    return out


def veg_height(evh):
    """LANDFIRE EVH code -> vegetation height (m). 101-199 tree (v-100 m), 201-299 shrub ((v-200)/10), 301-399 herb
    ((v-300)/10); developed-upland forest (13-15) assumed 10 m urban trees; everything else 0."""
    v = np.asarray(evh, dtype=float)
    h = np.zeros(v.shape)
    h = np.where((v >= 101) & (v <= 199), v - 100, h)
    h = np.where((v >= 201) & (v <= 299), (v - 200) / 10.0, h)
    h = np.where((v >= 301) & (v <= 399), (v - 300) / 10.0, h)
    h = np.where((v >= 13) & (v <= 15), 10.0, h)
    return h


@dataclass
class Topology:
    closed: np.ndarray  # bool per branch
    bus_on: np.ndarray  # bool per bus


class LAGrid:
    def __init__(self, g: dict | None = None):
        g = g or D.grid()
        self.g = g
        self.buses = g["buses"]
        self.branches = g["branches"]
        self.bix = {b["id"]: i for i, b in enumerate(self.buses)}
        self.lix = {l["id"]: i for i, l in enumerate(self.branches)}
        nb, nl = len(self.buses), len(self.branches)
        self.nb, self.nl = nb, nl
        br = self.branches
        self.f = np.array([self.bix[l["from_bus"]] for l in br])
        self.t = np.array([self.bix[l["to_bus"]] for l in br])
        self.x = np.array([l["x_pu"] for l in br])
        self.kv = np.array([l["kv"] for l in br])
        self.rating = np.array([l["rating_a"] for l in br])
        self.circuits = np.array([l["circuits"] for l in br], dtype=float)
        # plan-to-peak uprates (data/sanity_grid.py) model extra conductor: current per conductor divides by this
        self.thermal_scale = np.array([l.get("thermal_scale", 1.0) for l in br], dtype=float)
        self.cable = np.array([l["cable"] for l in br])
        self.length_km = np.array([l["length_m"] for l in br]) / 1000.0
        cond = [TH.CONDUCTORS[l["conductor"]] for l in br]
        self.tmax = np.array([c["tmax"] for c in cond])
        # ---- tower points (flattened)
        tb, lat, lon, el, zone, can, az, spanm, sagref, att, row = ([] for _ in range(11))
        for i, l in enumerate(br):
            T = l["towers"]
            n = len(T["lat"])
            tb += [i] * n
            lat += T["lat"]
            lon += T["lon"]
            el += T["elev"]
            zone += [ZONES.index(z) for z in T["zone"]]
            can += T["canopy"]
            az += T["az"]
            spanm += [l["length_m"] / max(n - 1, 1) / 1000.0] * n
            sagref += [l["sag_ref_m"]] * n
            att += [l["attach_m"]] * n
            row += [l["row_half_m"]] * n
        self.tb = np.array(tb)
        self.tlat, self.tlon = np.array(lat), np.array(lon)
        self.telev = np.maximum(np.array(el), 0.0)
        self.tzone = np.array(zone)
        self.tcanopy = np.array(can)
        self.taz = np.array(az)
        self.tspan_km = np.array(spanm)
        self.tsagref = np.array(sagref)
        self.tattach = np.array(att)
        self.trow = np.array(row)
        self.tspan_m = np.array([br[i]["span_m"] for i in tb], dtype=float)
        self.tfuel = np.array([FUEL[ZONES[z]] for z in zone]) * (~self.cable[self.tb])  # legacy (v1/v2) zone fuel
        self.tcanopy_zone = self.tcanopy.copy()
        self.tfuel_ign = self.tfuel.copy()
        self.tfbfm = np.zeros(len(tb), dtype=int)
        self.landfire = False
        lf = D.P / "landfire_towers.npz"
        if lf.exists():
            z = np.load(lf)
            if int(z["n_towers"]) == len(tb):
                self.landfire = True
                # vegetation top at the line (30 m pixel under the tower), replaces the per-zone constant
                self.tcanopy = veg_height(z["EVH_c"])
                # fuel receptivity: the most receptive fuel at the tower or 45 m either side (where sparks land)
                rec = np.maximum.reduce([fuel_receptivity(z[f"FBFM40_{k}"]) for k in ("c", "l", "r")])
                self.tfuel_ign = rec * (~self.cable[self.tb])
                self.tfbfm = z["FBFM40_c"]
        self.tcond = {k: np.array([TH.CONDUCTORS[br[i]["conductor"]][k] for i in tb], dtype=float)
                      for k in ("D", "R1", "R2", "T1", "T2", "eps", "alpha", "w_N_m", "n")}
        # ---- loads, generation, imports
        self.share = {
            "LDWP": np.array([b["load_share"] if b.get("ba") == "LDWP" else 0.0 for b in self.buses]),
            "SCE": np.array([b["load_share"] if b.get("ba") == "SCE" else 0.0 for b in self.buses]),
        }
        self.ba_scale = g["ba_scale"]
        self.gen_bus = np.array([self.bix[x["bus"]] for x in g["generators"]])
        self.gen_pmax = np.array([x["pmax_mw"] for x in g["generators"]])
        self.imp_bus = np.array([self.bix[x["bus"]] for x in g["imports"]])
        self.imp_kv = np.array([x["kv"] for x in g["imports"]])
        self.bus_county = self._bus_counties()
        self._ptdf_cache: dict = {}

    def _bus_counties(self):
        from scipy.spatial import cKDTree

        pop = D.population()
        tree = cKDTree(np.c_[pop.lat.values, pop.lon.values * np.cos(np.radians(34.0))])
        _, i = tree.query(np.c_[[b["lat"] for b in self.buses], np.array([b["lon"] for b in self.buses]) * np.cos(np.radians(34.0))])
        return np.array(["06" + str(pop.county.values[j]).zfill(3) for j in i])

    def base_topology(self) -> Topology:
        return Topology(np.ones(self.nl, bool), np.ones(self.nb, bool))

    # ------------------------------------------------------------ DC power flow
    def islands(self, top: Topology):
        on = top.closed & top.bus_on[self.f] & top.bus_on[self.t]
        parent = np.arange(self.nb)

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for a, b in zip(self.f[on], self.t[on]):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        roots = np.array([find(i) for i in range(self.nb)])
        return on, roots

    def ptdf(self, top: Topology):
        """PTDF (nl x nb) with one slack per island (import bus with highest kV, else largest plant)."""
        key = (top.closed.tobytes(), top.bus_on.tobytes())
        if key in self._ptdf_cache:
            return self._ptdf_cache[key]
        on, roots = self.islands(top)
        H = np.zeros((self.nl, self.nb))
        slack_of_island, sourced = {}, np.zeros(self.nb, bool)
        for r in np.unique(roots[top.bus_on]):
            members = np.where((roots == r) & top.bus_on)[0]
            mset = set(members.tolist())
            imps = [(self.imp_kv[k], b) for k, b in enumerate(self.imp_bus) if b in mset]
            gens = [(self.gen_pmax[k], b) for k, b in enumerate(self.gen_bus) if b in mset]
            if imps:
                slack = max(imps)[1]
            elif gens:
                slack = max(gens)[1]
            else:
                continue  # no source: island load is unserved
            slack_of_island[r] = slack
            sourced[members] = True
            if len(members) == 1:
                continue
            idx = {b: i for i, b in enumerate(members)}
            lines = np.where(on & np.isin(self.f, members))[0]
            n = len(members)
            B = np.zeros((n, n))
            fi = np.array([idx[b] for b in self.f[lines]])
            ti = np.array([idx[b] for b in self.t[lines]])
            y = 1.0 / self.x[lines]
            np.add.at(B, (fi, fi), y)
            np.add.at(B, (ti, ti), y)
            np.add.at(B, (fi, ti), -y)
            np.add.at(B, (ti, fi), -y)
            keep = np.array([i for i in range(n) if members[i] != slack])
            Xr = np.zeros((n, n))
            Xr[np.ix_(keep, keep)] = np.linalg.inv(B[np.ix_(keep, keep)])
            # flow_l = y_l (theta_f - theta_t); theta = Xr @ P
            Hl = (Xr[fi, :] - Xr[ti, :]) * y[:, None]
            H[np.ix_(lines, members)] = Hl
        out = (H, sourced, slack_of_island, roots)
        self._ptdf_cache[key] = out
        return out

    def injections(self, ldwp_mw, sce_mw, top: Topology, shed=None, imp_override=None):
        """Bus injections (hours x nb) in MW. Loads from BA demand x population shares; in-basin plants supply
        LOCAL_GEN_FRACTION of load pro rata to nameplate; imports split evenly over import buses (slack absorbs residual).
        imp_override {bus_index: MW} redispatches individual import points (operator action); the island slack absorbs
        the difference."""
        ldwp_mw = np.atleast_1d(np.asarray(ldwp_mw, float))
        sce_mw = np.atleast_1d(np.asarray(sce_mw, float))
        load = ldwp_mw[:, None] * self.share["LDWP"][None] * self.ba_scale["LDWP"] + sce_mw[:, None] * self.share["SCE"][None] * self.ba_scale["SCE"]
        if shed:
            for b, mw in shed.items():
                load[:, self.bix[b]] = np.maximum(load[:, self.bix[b]] - mw, 0.0)
        load = load * top.bus_on[None]
        total = load.sum(1)
        gen_on = top.bus_on[self.gen_bus]
        cap = (self.gen_pmax * gen_on).sum()
        frac = np.minimum(LOCAL_GEN_FRACTION * total / max(cap, 1.0), 0.95)
        P = -load
        np.add.at(P, (slice(None), self.gen_bus), (frac[:, None] * self.gen_pmax[None] * gen_on[None]))
        imp_on = top.bus_on[self.imp_bus]
        n_imp = max(imp_on.sum(), 1)
        imp_total = total - (frac * cap)
        np.add.at(P, (slice(None), self.imp_bus), (imp_total[:, None] / n_imp) * imp_on[None])
        for b, mw in (imp_override or {}).items():
            share = (imp_total / n_imp) * float(top.bus_on[b])
            P[:, b] += np.asarray(mw, float) - share  # replace this import point's even share with the dispatched MW
        return P, load

    def flows(self, P, top: Topology):
        H, sourced, _, _ = self.ptdf(top)
        Pm = P * sourced[None]
        return Pm @ H.T, sourced


# ---------------------------------------------------------------- weather at towers
def interp_weights(lats, lons, glats, glons):
    fy = np.clip((lats - glats[0]) / (glats[1] - glats[0]), 0, len(glats) - 1.001)
    fx = np.clip((lons - glons[0]) / (glons[1] - glons[0]), 0, len(glons) - 1.001)
    j, i = np.floor(fy).astype(int), np.floor(fx).astype(int)
    return j, i, fy - j, fx - i


def bilinear(arr, w):
    """arr (T, ny, nx) -> (T, npts)."""
    j, i, v, u = w
    return (arr[:, j, i] * (1 - u) * (1 - v) + arr[:, j, i + 1] * u * (1 - v)
            + arr[:, j + 1, i] * (1 - u) * v + arr[:, j + 1, i + 1] * u * v)


def tower_weather(grid: LAGrid, wx: dict, hours_idx, cal=None):
    cal = cal or D.calibration()
    w = interp_weights(grid.tlat, grid.tlon, wx["lats"], wx["lons"])
    sel = lambda k: wx[k][hours_idx]  # noqa: E731
    out = {
        "temp": bilinear(sel("temperature_2m"), w),
        "rh": bilinear(sel("relative_humidity_2m"), w),
        "wind": bilinear(sel("wind_speed_10m"), w),
        "gust_raw": bilinear(sel("wind_gusts_10m"), w),
        "ghi": bilinear(sel("shortwave_radiation"), w) if "shortwave_radiation" in wx else 0.0,
    }
    rad = np.radians(sel("wind_direction_10m"))
    u = bilinear(np.sin(rad), w)
    v = bilinear(np.cos(rad), w)
    out["wdir"] = (np.degrees(np.arctan2(u, v)) + 360.0) % 360.0
    a = np.array([cal["gust"]["a"][z] for z in ZONES])[grid.tzone]
    b = np.array([cal["gust"]["b"][z] for z in ZONES])[grid.tzone]
    out["gust"] = np.maximum(a[None] + b[None] * out["gust_raw"], out["wind"])
    return out


# ---------------------------------------------------------------- full hindcast for a set of hours
def hindcast(grid: LAGrid, wx: dict, hours_idx, ldwp_mw, sce_mw, top: Topology | None = None, shed=None, cal=None,
             thermal=True, imp_override=None):
    """Returns dict of arrays. Shapes: per-tower (H, ntower), per-line (H, nl), per-hour (H,)."""
    cal = cal or D.calibration()
    top = top or grid.base_topology()
    hours_idx = np.atleast_1d(hours_idx)
    W = tower_weather(grid, wx, hours_idx, cal)
    P, load = grid.injections(ldwp_mw, sce_mw, top, shed, imp_override)
    F, sourced = grid.flows(P, top)  # MW (H, nl)
    on, _ = grid.islands(top)
    F = F * on[None]
    amps = np.abs(F) * 1e6 / (np.sqrt(3) * grid.kv[None] * 1e3)
    loading = amps / grid.rating[None] * 100.0
    # per-circuit current at each tower
    I_t = (amps / (grid.circuits * grid.thermal_scale)[None])[:, grid.tb]
    d = np.abs(((W["wdir"] - grid.taz[None]) % 180.0))
    phi = np.radians(np.minimum(d, 180.0 - d))  # wind vs conductor axis
    if thermal:
        Tc = TH.conductor_temperature(grid.tcond, I_t, W["temp"], np.maximum(W["wind"], 0.5), phi, W["ghi"], grid.telev[None], iters=30)
    else:
        Tc = W["temp"] + 10.0
    sag = TH.sag(Tc, grid.tspan_m[None], grid.tsagref[None])
    gperp = W["gust"] * np.sin(phi)
    theta = TH.blowout_angle(gperp, grid.tcond["D"][None], grid.tcond["w_N_m"][None])
    clr = TH.clearance(sag, theta, grid.tattach[None], grid.trow[None], grid.tcanopy[None])
    fc = cal["fire"]
    if fc.get("model") == "two_step":
        # P(fire) = P(fault | wind, vegetation) x P(ignition | dryness, fuel)   (v3; validation/DECISIONS.md)
        st = cal["storm"]
        p_fault = damage_prob(W["gust"], clr, st["onset"], st.get("c50", 5.0))
        p_ign = sigmoid((fc["h50"] - W["rh"]) / 5.0) * grid.tfuel_ign[None]
        risk = p_fault * p_ign
    else:  # legacy v1/v2 blended score, with the per-zone canopy it was calibrated on
        clr_z = TH.clearance(sag, theta, grid.tattach[None], grid.trow[None], grid.tcanopy_zone[None])
        risk = (sigmoid((W["gust"] - fc["g50"]) / 2.0) * sigmoid((fc["h50"] - W["rh"]) / 5.0)
                * (0.5 + 0.5 * sigmoid((fc["c50"] - clr_z) / 1.0)) * grid.tfuel[None])
    energized = on[grid.tb][None]
    risk = risk * energized
    nl = grid.nl
    H = len(hours_idx)

    def by_line(vals, how):
        out = np.full((H, nl), -np.inf if how == "max" else np.inf)
        if how == "max":
            np.maximum.at(out, (slice(None), grid.tb), vals)
        else:
            np.minimum.at(out, (slice(None), grid.tb), vals)
        return out

    line_risk = by_line(risk, "max")
    line_temp = by_line(Tc, "max")
    line_clr = by_line(np.where(grid.cable[grid.tb][None], 99.0, clr), "min")
    line_sag = by_line(sag, "max")
    line_gust = by_line(W["gust"], "max")
    line_rh = by_line(-W["rh"], "max") * -1
    ri = (risk * grid.tspan_km[None]).sum(1)
    unserved = (load * (~sourced)[None]).sum(1)
    return {
        "hours_idx": hours_idx, "flow_mw": F, "amps": amps, "loading": loading, "line_temp": line_temp,
        "line_clear": line_clr, "line_sag": line_sag, "line_risk": line_risk, "line_gust": line_gust, "line_rh": line_rh,
        "tower_risk": risk, "tower_gust": W["gust"], "tower_gust_raw": W["gust_raw"], "tower_rh": W["rh"], "tower_temp": W["temp"],
        "region_index": ri, "load_mw": load.sum(1), "unserved_mw": unserved, "energized": on,
    }


def event_demand(times_utc):
    """Actual BA demand (EIA-930) at given UTC hours; NaN-free (forward/back filled). Returns (ldwp, sce)."""
    import pandas as pd

    dem = D.demand()
    idx = pd.DatetimeIndex(np.asarray(times_utc).astype("datetime64[ns]")).tz_localize("UTC")
    sub = dem.reindex(dem.index.union(idx)).sort_index().interpolate(limit_direction="both").reindex(idx)
    return sub["LDWP"].values, sub["SCE"].values


def hours_for(wx, start_utc, end_utc):
    t = wx["time"]
    s = np.datetime64(start_utc.replace(tzinfo=None), "h")
    e = np.datetime64(end_utc.replace(tzinfo=None), "h")
    return np.where((t >= s) & (t < e))[0]
