"""Build a simplified but geographically real transmission model from OSM (+HIFLD voltages), EIA-860 plants,
Census population and terrain.  ->  data/processed/grid_<region>.json  +  data/processed/grid_report.md

Everything electrical here is MODELED from voltage class (conductor, reactance, rating, span, sag, height).
Steps follow the Phase-2 spec; every guess is listed in the report and validation/DECISIONS.md.
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.ndimage import distance_transform_edt, map_coordinates
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fetch"))
from common import PROCESSED, RAW, log, region  # noqa: E402

from backend.thermal import CONDUCTORS, static_rating  # noqa: E402

L = log("build_grid")

# ---------------------------------------------------------------- voltage-class assumptions (MODELED)
# kv_class: conductor, reactance ohm/km, span m, attach height m, ROW half-width to vegetation m
CLASS = {
    66: {"conductor": "Linnet", "x_ohm_km": 0.45, "span": 300, "attach": 20.0, "row": 7.0},
    115: {"conductor": "Hawk", "x_ohm_km": 0.45, "span": 300, "attach": 25.0, "row": 10.0},
    220: {"conductor": "Drake", "x_ohm_km": 0.40, "span": 400, "attach": 35.0, "row": 15.0},
    500: {"conductor": "Drake3", "x_ohm_km": 0.33, "span": 400, "attach": 40.0, "row": 25.0},
}
CABLE_X_OHM_KM = 0.15
CANOPY_BY_ZONE = {"mountain": 6.0, "valley": 10.0, "basin": 9.0, "coast": 7.0}  # m; NLCD fallback (DECISIONS.md)


def kv_class(kv: float) -> int:
    return 66 if kv < 100 else 115 if kv < 180 else 220 if kv < 400 else 500


def parse_kv(v: str | None) -> float | None:
    if not v:
        return None
    vals = []
    for p in str(v).replace("/", ";").split(";"):
        try:
            x = float(p)
            if x > 0:
                vals.append(x / 1000.0)
        except ValueError:
            pass
    return max(vals) if vals else None


class Proj:
    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110540.0

    def xy(self, lat, lon):
        return (np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * self.ky

    def pts(self, lat, lon):
        """(n, 2) array of local meters."""
        x, y = self.xy(lat, lon)
        return np.c_[np.atleast_1d(x), np.atleast_1d(y)]


def polyline_len(xy):
    d = np.diff(xy, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def main():
    reg = region()
    b = reg["bbox"]
    proj = Proj((b["lat_min"] + b["lat_max"]) / 2, (b["lon_min"] + b["lon_max"]) / 2)
    osm = json.loads((RAW / "osm" / "power.json").read_text())["elements"]
    report = defaultdict(list)
    counts = Counter()

    # ------------------------------------------------------------ substations / plants (snap targets)
    subs = []
    for e in osm:
        t = e.get("tags", {})
        if t.get("power") not in ("substation", "plant"):
            continue
        if e["type"] == "node":
            lats, lons = [e["lat"]], [e["lon"]]
        elif "geometry" in e:
            lats = [g["lat"] for g in e["geometry"]]
            lons = [g["lon"] for g in e["geometry"]]
        elif "bounds" in e:
            bb = e["bounds"]
            lats, lons = [bb["minlat"], bb["maxlat"]], [bb["minlon"], bb["maxlon"]]
        else:
            continue
        x, y = proj.xy(lats, lons)
        subs.append({"osm": f"{e['type']}/{e['id']}", "kind": t["power"], "name": t.get("name"), "operator": t.get("operator"),
                     "kv": parse_kv(t.get("voltage")), "lat": float(np.mean(lats)), "lon": float(np.mean(lons)),
                     "x": float(np.mean(x)), "y": float(np.mean(y)),
                     "box": (float(np.min(x)), float(np.min(y)), float(np.max(x)), float(np.max(y)))})
    L.info("snap targets: %d substations/plants", len(subs))
    # merge substations closer than 300 m (union-find on centroids)
    sxy = np.array([[s["x"], s["y"]] for s in subs])
    parent = list(range(len(subs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j in cKDTree(sxy).query_pairs(300.0):
        parent[find(i)] = find(j)
    clusters = defaultdict(list)
    for i in range(len(subs)):
        clusters[find(i)].append(i)
    counts["substations_merged_away"] = len(subs) - len(clusters)

    def box_dist(px, py, box):
        dx = max(box[0] - px, 0, px - box[2])
        dy = max(box[1] - py, 0, py - box[3])
        return math.hypot(dx, dy)

    sub_tree = cKDTree(sxy)

    def snap(px, py):
        best = None
        for i in sub_tree.query_ball_point([px, py], 2500.0):
            d = box_dist(px, py, subs[i]["box"])
            if d <= 500.0 and (best is None or d < best[0]):
                best = (d, i)
        return None if best is None else find(best[1])

    # ------------------------------------------------------------ HIFLD voltage fill
    hifld = []
    hf = PROCESSED / f"hifld_lines_{reg['name']}.geojson"
    if hf.exists():
        for f in json.loads(hf.read_text())["features"]:
            g = f["geometry"]
            parts = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
            for p in parts:
                for lon, lat in p[:: max(1, len(p) // 20)]:
                    x, y = proj.xy(lat, lon)
                    hifld.append((float(x), float(y), f["properties"].get("VOLTAGE"), f["properties"].get("OWNER")))
    htree = cKDTree(np.array([[h[0], h[1]] for h in hifld])) if hifld else None

    # ------------------------------------------------------------ line ways
    ways = []
    for e in osm:
        t = e.get("tags", {})
        if t.get("power") not in ("line", "cable") or "geometry" not in e:
            continue
        lat = np.array([g["lat"] for g in e["geometry"]])
        lon = np.array([g["lon"] for g in e["geometry"]])
        x, y = proj.xy(lat, lon)
        kv = parse_kv(t.get("voltage"))
        src = "osm"
        if kv is None and htree is not None:
            d, i = htree.query([x[len(x) // 2], y[len(y) // 2]])
            if d < 150 and hifld[i][2] and hifld[i][2] > 0:
                kv, src = float(hifld[i][2]), "hifld"
                counts["voltage_filled_from_hifld"] += 1
        if kv is None:
            counts["dropped_unknown_voltage"] += 1
            continue
        if kv < reg["min_voltage_kv"]:
            counts["dropped_below_66kv"] += 1
            continue
        try:
            circuits = max(1, int(str(t.get("circuits", "1")).split(";")[0]))
        except ValueError:
            circuits = 1
        ways.append({"osm": e["id"], "kv": kv, "cable": t["power"] == "cable", "circuits": circuits,
                     "name": t.get("name"), "operator": t.get("operator"), "kv_src": src,
                     "lat": lat, "lon": lon, "x": x, "y": y})
    L.info("ways kept (>= %d kV): %d", reg["min_voltage_kv"], len(ways))

    # ------------------------------------------------------------ node keys, T-junction splits
    def ckey(la, lo):
        return f"P{la:.5f},{lo:.5f}"

    endpoint_keys = Counter()
    for w in ways:
        for k in (0, -1):
            endpoint_keys[ckey(w["lat"][k], w["lon"][k])] += 1
    pieces = []
    for w in ways:
        cuts = [0] + [i for i in range(1, len(w["lat"]) - 1) if ckey(w["lat"][i], w["lon"][i]) in endpoint_keys] + [len(w["lat"]) - 1]
        for a, c in zip(cuts[:-1], cuts[1:]):
            if c > a:
                pieces.append({**w, "lat": w["lat"][a:c + 1], "lon": w["lon"][a:c + 1], "x": w["x"][a:c + 1], "y": w["y"][a:c + 1]})
    counts["t_junction_splits"] = len(pieces) - len(ways)

    def node_of(la, lo, px, py):
        s = snap(px, py)
        return f"S{s}" if s is not None else ckey(la, lo)

    edges = []
    for p in pieces:
        a = node_of(p["lat"][0], p["lon"][0], p["x"][0], p["y"][0])
        c = node_of(p["lat"][-1], p["lon"][-1], p["x"][-1], p["y"][-1])
        if a == c:
            counts["self_loops_dropped"] += 1
            continue
        edges.append({"a": a, "b": c, "kv": p["kv"], "cable": p["cable"], "circuits": p["circuits"], "names": {p["name"]} - {None},
                      "operators": {p["operator"]} - {None}, "osm": [p["osm"]],
                      "lat": list(p["lat"]), "lon": list(p["lon"])})

    def is_sub(n):
        return n.startswith("S")

    # ------------------------------------------------------------ contract degree-2 chains through non-substation nodes
    def incidence():
        inc = defaultdict(list)
        for i, e in enumerate(edges):
            if e is not None:
                inc[e["a"]].append(i)
                inc[e["b"]].append(i)
        return inc

    changed = True
    while changed:
        changed = False
        inc = incidence()
        for n, es in inc.items():
            es = [i for i in es if edges[i] is not None]
            if is_sub(n) or len(es) != 2:
                continue
            i, j = es
            e1, e2 = edges[i], edges[j]
            if e1 is None or e2 is None or i == j or kv_class(e1["kv"]) != kv_class(e2["kv"]):
                continue
            # orient so that e1 ends at n and e2 starts at n
            l1 = list(zip(e1["lat"], e1["lon"])) if e1["b"] == n else list(zip(e1["lat"], e1["lon"]))[::-1]
            a1 = e1["a"] if e1["b"] == n else e1["b"]
            l2 = list(zip(e2["lat"], e2["lon"])) if e2["a"] == n else list(zip(e2["lat"], e2["lon"]))[::-1]
            b2 = e2["b"] if e2["a"] == n else e2["a"]
            if a1 == b2:
                continue
            merged = l1 + l2[1:]
            edges[i] = {"a": a1, "b": b2, "kv": max(e1["kv"], e2["kv"]), "cable": e1["cable"] and e2["cable"],
                        "circuits": min(e1["circuits"], e2["circuits"]), "names": e1["names"] | e2["names"],
                        "operators": e1["operators"] | e2["operators"], "osm": e1["osm"] + e2["osm"],
                        "lat": [m[0] for m in merged], "lon": [m[1] for m in merged]}
            edges[j] = None
            changed = True
    edges = [e for e in edges if e is not None]
    for e in edges:
        x, y = proj.xy(e["lat"], e["lon"])
        e["length_m"] = polyline_len(np.c_[x, y])

    # ------------------------------------------------------------ drop dangling fragments < 1 km (iterate)
    edge_margin_m = 1500.0
    bx0, by0 = proj.xy(b["lat_min"], b["lon_min"])
    bx1, by1 = proj.xy(b["lat_max"], b["lon_max"])

    def near_edge(lat, lon):
        x, y = proj.xy(lat, lon)
        return min(x - bx0, bx1 - x, y - by0, by1 - y) < edge_margin_m

    while True:
        deg = Counter()
        for e in edges:
            deg[e["a"]] += 1
            deg[e["b"]] += 1
        drop = [k for k, e in enumerate(edges) if e["length_m"] < 1000 and any(
            (not is_sub(n)) and deg[n] == 1 and not near_edge(*(e["lat"][0], e["lon"][0]) if n == e["a"] else (e["lat"][-1], e["lon"][-1]))
            for n in (e["a"], e["b"]))]
        if not drop:
            break
        counts["dangling_fragments_dropped"] += len(drop)
        edges = [e for k, e in enumerate(edges) if k not in set(drop)]

    # ------------------------------------------------------------ merge parallel branches
    groups = defaultdict(list)
    for e in edges:
        groups[(tuple(sorted((e["a"], e["b"]))), kv_class(e["kv"]), e["cable"])].append(e)
    merged = []
    for (_, _, _), es in groups.items():
        base = max(es, key=lambda e: e["length_m"])
        base = dict(base)
        base["circuits"] = sum(e["circuits"] for e in es)
        base["names"] = set().union(*[e["names"] for e in es])
        base["osm"] = sum((e["osm"] for e in es), [])
        merged.append(base)
    counts["parallel_ways_merged"] = len(edges) - len(merged)
    edges = merged

    # ------------------------------------------------------------ buses
    nodes = sorted({e["a"] for e in edges} | {e["b"] for e in edges})
    node_pos = {}
    for e in edges:
        node_pos.setdefault(e["a"], (e["lat"][0], e["lon"][0]))
        node_pos.setdefault(e["b"], (e["lat"][-1], e["lon"][-1]))
    bus_kv = defaultdict(float)
    for e in edges:
        bus_kv[e["a"]] = max(bus_kv[e["a"]], e["kv"])
        bus_kv[e["b"]] = max(bus_kv[e["b"]], e["kv"])

    # keep components; drop isolated fragments without any substation
    adj = defaultdict(set)
    for e in edges:
        adj[e["a"]].add(e["b"])
        adj[e["b"]].add(e["a"])
    seen, comps = set(), []
    for n in nodes:
        if n in seen:
            continue
        st, comp = [n], []
        seen.add(n)
        while st:
            u = st.pop()
            comp.append(u)
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    st.append(v)
        comps.append(comp)
    comps.sort(key=len, reverse=True)
    keep_nodes = set()
    for c in comps:
        if c is comps[0] or (len(c) >= 4 and any(is_sub(n) for n in c)):
            keep_nodes |= set(c)
        else:
            counts["small_islands_dropped_buses"] += len(c)
    edges = [e for e in edges if e["a"] in keep_nodes]
    nodes = [n for n in nodes if n in keep_nodes]
    counts["islands_kept"] = sum(1 for c in comps if set(c) <= keep_nodes)

    bus_id = {n: f"B{i + 1}" for i, n in enumerate(nodes)}
    buses = []
    for n in nodes:
        if is_sub(n):
            idx = clusters[int(n[1:])]
            members = [subs[i] for i in idx]
            named = [m for m in members if m["name"]]
            lat = float(np.mean([m["lat"] for m in members]))
            lon = float(np.mean([m["lon"] for m in members]))
            name = (named[0]["name"] if named else None) or f"Substation {bus_id[n]}"
            ops = {m["operator"] for m in members if m["operator"]}
            kind = "plant" if all(m["kind"] == "plant" for m in members) else "substation"
        else:
            lat, lon = node_pos[n]
            name, ops, kind = f"Junction {bus_id[n]}", set(), "junction"
        boundary = near_edge(lat, lon) and bus_kv[n] >= 200
        buses.append({"id": bus_id[n], "name": name, "lat": round(lat, 6), "lon": round(lon, 6), "kv": bus_kv[n],
                      "kind": kind, "operator": "; ".join(sorted(ops)) or None, "boundary": bool(boundary)})
    bus_by = {bb["id"]: bb for bb in buses}

    # ------------------------------------------------------------ terrain sampling helpers
    ter = np.load(PROCESSED / f"terrain_{reg['name']}.npz")
    elev, tlats, tlons = ter["elev"], ter["lats"], ter["lons"]
    dlat, dlon = tlats[1] - tlats[0], tlons[1] - tlons[0]
    sea = elev <= 0
    cell_m = dlat * 110540.0
    dist_sea = distance_transform_edt(~sea) * cell_m
    gy, gx = np.gradient(elev, cell_m, dlon * proj.kx)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))

    def sample(grid, lat, lon):
        return map_coordinates(grid, [(np.asarray(lat) - tlats[0]) / dlat, (np.asarray(lon) - tlons[0]) / dlon], order=1, mode="nearest")

    def zone_of(el, ds, sl):
        z = np.where(el >= 500, "mountain", np.where((el >= 300) & (sl >= 10), "mountain",
                     np.where(el >= 150, "valley", np.where((ds < 4000) & (el < 60), "coast", "basin"))))
        return z

    # ------------------------------------------------------------ branches with electrical params + tower points
    branches = []
    for k, e in enumerate(edges):
        cls = kv_class(e["kv"])
        p = CLASS[cls]
        cond = CONDUCTORS[p["conductor"]]
        n_circ = e["circuits"]
        x_ohm = (CABLE_X_OHM_KM if e["cable"] else p["x_ohm_km"]) * e["length_m"] / 1000.0
        zbase = e["kv"] ** 2 / 100.0
        x_pu = max(1e-4, x_ohm / zbase / n_circ)
        rating = static_rating(cond) * n_circ
        lat, lon = np.array(e["lat"]), np.array(e["lon"])
        x, y = proj.xy(lat, lon)
        seg = np.r_[0, np.cumsum(np.hypot(np.diff(x), np.diff(y)))]
        n_t = max(2, int(round(seg[-1] / p["span"])) + 1)
        s = np.linspace(0, seg[-1], n_t)
        tlat, tlon = np.interp(s, seg, lat), np.interp(s, seg, lon)
        tx, ty = np.interp(s, seg, x), np.interp(s, seg, y)
        az = np.degrees(np.arctan2(np.gradient(tx), np.gradient(ty))) % 180.0  # conductor azimuth (deg from north)
        el, ds, sl = sample(elev, tlat, tlon), sample(dist_sea, tlat, tlon), sample(slope, tlat, tlon)
        zones = zone_of(el, ds, sl)
        canopy = np.array([CANOPY_BY_ZONE[z] for z in zones])
        if e["cable"]:
            canopy[:] = 0.0
        zmaj = Counter(zones.tolist()).most_common(1)[0][0]
        # simplified geometry for rendering (every ~250 m)
        keep = np.unique(np.r_[0, np.searchsorted(seg, np.arange(0, seg[-1], 250.0)), len(seg) - 1])
        branches.append({
            "id": f"L{k + 1}", "from_bus": bus_id[e["a"]], "to_bus": bus_id[e["b"]], "kv": e["kv"], "kv_class": cls,
            "cable": e["cable"], "circuits": n_circ, "name": "; ".join(sorted(e["names"]))[:120] or None,
            "operator": "; ".join(sorted(e["operators"]))[:80] or None, "osm_ways": e["osm"][:20],
            "length_m": round(e["length_m"], 1), "x_pu": round(x_pu, 6), "rating_a": round(rating, 1),
            "conductor": p["conductor"], "span_m": p["span"], "attach_m": p["attach"], "row_half_m": p["row"],
            "sag_ref_m": round(0.02 * p["span"], 2), "zone": zmaj,
            "geometry": [[round(float(lat[i]), 5), round(float(lon[i]), 5)] for i in keep],
            "towers": {"lat": np.round(tlat, 5).tolist(), "lon": np.round(tlon, 5).tolist(), "elev": np.round(el, 1).tolist(),
                       "zone": zones.tolist(), "slope": np.round(sl, 1).tolist(), "canopy": canopy.tolist(),
                       "az": np.round(az, 1).tolist()},
        })

    # ------------------------------------------------------------ loads: Voronoi by population
    pop = pd.read_csv(PROCESSED / f"population_{reg['name']}.csv")
    load_buses = [bb for bb in buses if bb["kind"] == "substation" and bb["kv"] <= 240]
    lb_xy = proj.pts([bb["lat"] for bb in load_buses], [bb["lon"] for bb in load_buses])
    _, near = cKDTree(lb_xy).query(proj.pts(pop.lat.values, pop.lon.values))
    LDWP_WORDS = ("water and power", "water & power", "ladwp", "burbank", "glendale")
    # BA by operator tag; untagged substations inherit the BA of the nearest tagged substation
    tagged = [(i, bb) for i, bb in enumerate(load_buses) if bb["operator"]]
    t_xy = lb_xy[[i for i, _ in tagged]] if tagged else None
    t_tree = cKDTree(t_xy) if tagged else None
    for i, bb in enumerate(load_buses):
        op = (bb["operator"] or "").lower()
        if bb["operator"]:
            bb["ba"] = "LDWP" if any(w in op for w in LDWP_WORDS) else "SCE"
        elif t_xy is not None:
            _, j = t_tree.query(lb_xy[i])
            op2 = (tagged[j][1]["operator"] or "").lower()
            bb["ba"] = "LDWP" if any(w in op2 for w in LDWP_WORDS) else "SCE"
        else:
            bb["ba"] = "SCE"
    pop["bus"] = [load_buses[i]["id"] for i in near]
    pop["ba"] = [load_buses[i]["ba"] for i in near]
    bus_pop = pop.groupby("bus")["pop"].sum()
    ba_pop = pop.groupby("ba")["pop"].sum()
    for bb in buses:
        bb["pop"] = int(bus_pop.get(bb["id"], 0))
        bb.setdefault("ba", None)
        bb["load_share"] = round(bb["pop"] / ba_pop[bb["ba"]], 6) if bb["ba"] in ba_pop and bb["pop"] else 0.0
    SCE_SERVICE_POP = 15.0e6  # SCE serves ~15 million people (company figure); share of SCE sub-BA demand in bbox
    ba_scale = {"LDWP": 1.0, "SCE": round(float(ba_pop.get("SCE", 0)) / SCE_SERVICE_POP, 4)}

    # ------------------------------------------------------------ generators (EIA-860) + imports
    plants = pd.read_csv(PROCESSED / f"plants_{reg['name']}.csv")
    plants = plants[plants.nameplate_mw >= 20]
    btree = cKDTree(proj.pts([bb["lat"] for bb in buses], [bb["lon"] for bb in buses]))
    gens = []
    d, gi = btree.query(proj.pts(plants.lat.values, plants.lon.values))
    for (_, pl), dd, i in zip(plants.iterrows(), d, gi):
        gens.append({"id": f"G{len(gens) + 1}", "name": pl["name"], "bus": buses[i]["id"], "pmax_mw": float(pl.nameplate_mw),
                     "tech": pl.tech, "eia_plant_code": int(pl.plant_code), "snap_km": round(dd / 1000, 2), "kind": "plant"})
    bnd = sorted([bb for bb in buses if bb["boundary"]], key=lambda bb: -bb["kv"])
    if not bnd:
        bnd = sorted(buses, key=lambda bb: -bb["kv"])[:1]
    imports = [{"id": f"I{i + 1}", "name": f"Import @ {bb['name']}", "bus": bb["id"], "kv": bb["kv"]} for i, bb in enumerate(bnd)]

    # ------------------------------------------------------------ critical sites (OSM)
    crit = json.loads((RAW / "osm" / "critical.json").read_text())["elements"]
    sites = []
    lt = cKDTree(lb_xy)
    for e in crit:
        t = e.get("tags", {})
        la = e.get("lat") or e.get("center", {}).get("lat")
        lo = e.get("lon") or e.get("center", {}).get("lon")
        if la is None:
            continue
        kind = "hospital" if t.get("amenity") == "hospital" else "water"
        if kind == "hospital" and not t.get("name"):
            continue
        _, i = lt.query(proj.pts(la, lo)[0])
        sites.append({"name": t.get("name") or ("Water/wastewater plant"), "type": kind, "lat": la, "lon": lo,
                      "bus": load_buses[i]["id"], "backup_hours": 72 if kind == "hospital" else 24})

    grid = {
        "region": reg["name"], "bbox": b, "proj": {"lat0": proj.lat0, "lon0": proj.lon0},
        "modeled_note": "Line ratings, impedances, conductors, heights and load allocation are MODELED from voltage class and Census population, not utility data.",
        "buses": buses, "branches": branches, "generators": gens, "imports": imports, "critical_sites": sites,
        "ba_scale": ba_scale, "class_assumptions": CLASS, "canopy_by_zone_m": CANOPY_BY_ZONE,
    }
    out = PROCESSED / f"grid_{reg['name']}.json"
    out.write_text(json.dumps(grid))
    L.info("grid: %d buses, %d branches, %d generators, %d import points, %d critical sites -> %s",
           len(buses), len(branches), len(gens), len(imports), len(sites), out.name)
    write_report(grid, counts, ba_pop, reg)


def write_report(grid, counts, ba_pop, reg):
    br = grid["branches"]
    hist = Counter(b["kv_class"] for b in br)
    km = defaultdict(float)
    for b in br:
        km[b["kv_class"]] += b["length_m"] / 1000
    zones = Counter(b["zone"] for b in br)
    dem = pd.read_csv(PROCESSED / f"demand_{reg['name']}.csv.gz", parse_dates=["utc_time"])
    piv = dem.pivot_table(index="utc_time", columns="respondent", values="demand_mw")
    model_load = piv["LDWP"] * grid["ba_scale"]["LDWP"] + piv["SCE"] * grid["ba_scale"]["SCE"]
    lines = [
        f"# Grid build report: {reg['title']}", "",
        "> All electrical parameters (conductor, reactance, rating, heights, sag) are **modeled from voltage class**. "
        "Load is **allocated by 2020 Census tract population** (Voronoi cell of the nearest substation). Not utility data.", "",
        "## Counts", "",
        f"- Buses: {len(grid['buses'])} ({sum(b['kind'] == 'substation' for b in grid['buses'])} substations, "
        f"{sum(b['kind'] == 'plant' for b in grid['buses'])} plants, {sum(b['kind'] == 'junction' for b in grid['buses'])} junctions)",
        f"- Branches: {len(br)} ({sum(b['cable'] for b in br)} underground cables, kept in power flow, excluded from clearance/fire)",
        f"- Tower points: {sum(len(b['towers']['lat']) for b in br)}",
        f"- Generators (EIA-860, >= 20 MW): {len(grid['generators'])}, {sum(g['pmax_mw'] for g in grid['generators']):.0f} MW nameplate",
        f"- Import points (>= 200 kV buses at the bbox edge): {len(grid['imports'])}",
        f"- Critical sites (OSM): {sum(s['type'] == 'hospital' for s in grid['critical_sites'])} hospitals, "
        f"{sum(s['type'] == 'water' for s in grid['critical_sites'])} water/wastewater plants", "",
        "## Voltage classes", "", "| class | branches | km |", "|---|---|---|",
        *[f"| {k} kV | {hist[k]} | {km[k]:.0f} |" for k in sorted(hist)], "",
        "## Zones (majority per branch)", "", *[f"- {z}: {n}" for z, n in zones.most_common()], "",
        "## Load allocation", "",
        f"- Population in bbox by BA: " + ", ".join(f"{k} {v / 1e6:.2f} M" for k, v in ba_pop.items()),
        f"- Demand scale: LDWP BA x {grid['ba_scale']['LDWP']}, SCE sub-BA x {grid['ba_scale']['SCE']} (bbox SCE population / ~15 M SCE service population)",
        f"- Modeled regional load 2019-2024: median {model_load.median():.0f} MW, 99th pct {model_load.quantile(0.99):.0f} MW, "
        f"max {model_load.max():.0f} MW", "",
        "## Dropped or guessed", "", *[f"- {k.replace('_', ' ')}: {v}" for k, v in counts.items()],
        "- Transformers inside substations are not modeled (all voltage levels at a substation share one bus).",
        "- Tree canopy: constant per zone (" + ", ".join(f"{k} {v} m" for k, v in grid["canopy_by_zone_m"].items()) + "); NLCD not fetched (see DECISIONS.md).",
    ]
    (PROCESSED / "grid_report.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
