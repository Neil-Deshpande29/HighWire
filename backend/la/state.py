"""LAState: the real-LA grid at one replay hour, exposing the same interface the Phase-1 planner/tools use
(lines, buses, weather, sim, simulate(actions), reports_view, ...). All numbers come from the deterministic engine.

Extra action types:
  * PSPS-style plans:  {"type": "deenergize_lines", "target": "mountain lines over alarm", "targets": ["L12", ...]}
  * operator derate:   {"type": "limit_line", "target": "L12", "pct": 60}  caps that line at 60% of its modeled rating;
    the physics flags an overload when the flow exceeds the cap (it does not redispatch anything by itself).

Study mode (UI): `with_conditions({gust_pct, rh_pct, temp_c, demand_pct})` returns a copy of this hour with the model
INPUTS scaled (weather and demand); no model, threshold or calibration changes. The original state is never mutated.
"""
from __future__ import annotations

import copy
import json
import threading

import numpy as np
import pandas as pd

from . import buildings as B
from . import data as D
from . import engine as E

CUSTOMERS_PER_MW = 500
CUT_IMPACT_LINES = 8  # the riskiest lines get a "what if we cut this one" answer; the screen lists eight
# Utility-style safety policy (validation/DECISIONS.md, "Co-pilot safety policy"):
#  * a sub-transmission line (< 180 kV) above the fire alarm is a VIOLATION (de-energize it, PSPS style);
#  * a bulk 220-500 kV line above the alarm is a WATCH item (patrol/monitor): these are the import corridors that
#    feed the basin, and utilities keep them energized during PSPS (SCE's Jan 2025 PSPS cut 373 distribution circuits);
#  * overloads, conductor over max temperature, clearance < 3 m, and blackouts (> 10% of regional load unserved)
#    stay violations.
SUBTRANSMISSION_MAX_KV = 180.0
MAX_UNSERVED_FRACTION = 0.10
CONDITION_KEYS = ("gust_pct", "rh_pct", "temp_c", "demand_pct")
# weather variables read by engine.tower_weather for one hour; Study conditions scale these inputs only
_WX_HOURLY = ("temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
              "shortwave_radiation", "precipitation")


class LAState:
    def __init__(self, eid: str, t: str | None = None, grid: E.LAGrid | None = None):
        self.lock = threading.RLock()
        self.grid = grid or E.LAGrid()
        self.eid = eid
        self.wx = D.weather(eid)
        if eid == "FX":  # live forecast
            from .forecast import live_event

            ev = live_event()
        else:
            ev = D.events()[eid]
        hrs = E.hours_for(self.wx, ev["start_utc"], ev["end_utc"])
        times = pd.DatetimeIndex(self.wx["time"][hrs].astype("datetime64[ns]")).tz_localize("UTC")
        t = t or None  # empty string from a client means "first hour"
        k = 0 if t is None else int(np.clip(np.searchsorted(times, pd.Timestamp(t).tz_convert("UTC")), 0, len(times) - 1))
        self.hour_idx = int(hrs[k])
        self.t_utc = times[k]
        if eid == "FX":  # future hours have no metered demand: use the calibrated demand model on the forecast
            from .load_model import predict_from_weather

            dem = predict_from_weather(self.wx)
            ld, sc = dem["LDWP"][[self.hour_idx]], dem["SCE"][[self.hour_idx]]
        else:
            ld, sc = E.event_demand(self.wx["time"][[self.hour_idx]])
        self.ldwp, self.sce = float(ld[0]), float(sc[0])
        g = self.grid
        self.scenario = (f"LA {'FORECAST' if eid == 'FX' else 'replay ' + eid} @ {self.t_utc.tz_convert('America/Los_Angeles'):%Y-%m-%d %H:%M}")
        self.load_multiplier = 1.0
        self.operator_opened: list[str] = []
        self.deenergized: list[str] = []
        self.shed: dict[str, float] = {}
        self.caps: dict[str, float] = {}  # operator derates: line id -> % of modeled rating
        self.conditions = {k: 0.0 for k in CONDITION_KEYS}
        self.reports: list[dict] = []
        self.plans, self.plan_status, self.bulletin = {}, {}, None
        self.gens: dict = {}
        self._cut_cache: dict = {}
        crit = {}
        for c in D.grid()["critical_sites"]:
            crit.setdefault(c["bus"], c)
        P, load = g.injections(self.ldwp, self.sce, g.base_topology())
        self.buses = {}
        for i, b in enumerate(g.buses):
            c = crit.get(b["id"])
            self.buses[b["id"]] = {"id": b["id"], "name": b["name"], "zone": "basin", "critical": c is not None,
                                   "critical_type": (c["type"] if c else "none"), "critical_name": c["name"] if c else None,
                                   "backup_hours": float(c["backup_hours"]) if c else 0.0, "load_mw": float(load[0, i]),
                                   "elevation_m": 0.0, "flood_threshold_m": 99.0, "x": b["lon"], "y": b["lat"]}
        elev_by_line = np.zeros(g.nl)
        np.add.at(elev_by_line, g.tb, g.telev)
        elev_by_line /= np.bincount(g.tb, minlength=g.nl)
        self.lines = {l["id"]: {"id": l["id"], "from_bus": l["from_bus"], "to_bus": l["to_bus"], "zone": l["zone"], "tie": False,
                                "status": "closed", "mid_elevation_m": float(elev_by_line[i]), "max_temp_c": float(g.tmax[i]),
                                "kv": l["kv"], "name": l["name"], "cable": l["cable"]}
                      for i, l in enumerate(g.branches)}
        self.weather = self._zone_weather()
        self.recompute()

    # ------------------------------------------------------------------ weather summary per zone
    def _zone_weather(self):
        W = E.tower_weather(self.grid, self.wx, np.array([self.hour_idx]))
        out = {}
        for zi, z in enumerate(E.ZONES):
            m = self.grid.tzone == zi
            if not m.any():
                continue
            out[z] = {"air_temp_c": round(float(W["temp"][0, m].mean()), 1), "wind_speed_ms": round(float(W["wind"][0, m].mean()), 1),
                      "gust_ms": round(float(W["gust"][0, m].max()), 1), "humidity_pct": round(float(W["rh"][0, m].min()), 0),
                      "wind_dir_deg": round(float(np.median(W["wdir"][0, m])), 0), "solar_w_m2": round(float(np.mean(W["ghi"][0, m])), 0),
                      "flood_level_m": 0.0}
        return out

    # ------------------------------------------------------------------ physics
    def _topology(self, actions):
        g = self.grid
        top = g.base_topology()
        shed = dict(self.shed)
        caps = dict(self.caps)
        imp = dict(getattr(self, "imp_override", {}))
        imp_ids = {x["id"]: g.bix[x["bus"]] for x in D.grid()["imports"]}
        errors = []
        opened = set(self.operator_opened)
        deen = set(self.deenergized)
        for a in actions or []:
            t = a.get("type")
            if t == "open_line":
                opened.add(a.get("target"))
            elif t == "close_line":
                opened.discard(a.get("target"))
            elif t == "deenergize_lines":
                opened |= set(a.get("targets") or [])
            elif t == "deenergize_bus":
                deen.add(a.get("target"))
            elif t == "shed_load":
                shed[a["target"]] = shed.get(a["target"], 0.0) + float(a.get("mw", 0))
            elif t == "limit_line":  # operator derate: cap the line at pct of its modeled rating
                pct = float(a.get("pct", 100))
                if a.get("target") not in g.lix:
                    errors.append(f"Invalid action: unknown line {a.get('target')}")
                elif not 0 < pct <= 100:
                    errors.append(f"Invalid action: limit_line pct must be in (0, 100], got {pct:g}")
                else:
                    caps[a["target"]] = pct
            elif t == "dispatch_gen":
                if a.get("target") in imp_ids:  # redispatch an import point; the island slack absorbs the change
                    imp[imp_ids[a["target"]]] = max(0.0, float(a.get("mw", 0)))
                else:
                    errors.append("dispatch_gen on the LA grid applies to import points I1..In only (plants are dispatched pro rata)")
            else:
                errors.append(f"Invalid action type {t!r}")
        for lid in opened:
            if lid in g.lix:
                top.closed[g.lix[lid]] = False
            else:
                errors.append(f"Invalid action: unknown line {lid}")
        for bid in deen:
            if bid in g.bix:
                top.bus_on[g.bix[bid]] = False
            else:
                errors.append(f"Invalid action: unknown bus {bid}")
        return top, shed, errors, opened, deen, imp, caps

    def simulate(self, actions=None) -> dict:
        g = self.grid
        top, shed, errors, opened, deen, imp, caps = self._topology(actions)
        r = E.hindcast(g, self.wx, np.array([self.hour_idx]), self.ldwp, self.sce, top=top, shed=shed, imp_override=imp)
        cap_pct = np.full(g.nl, 100.0)  # operator derates (limit_line); loading is checked against the cap
        for lid, pct in caps.items():
            cap_pct[g.lix[lid]] = pct
        cal = D.calibration()["fire"]
        alarm = cal["line_alarm"]
        on = r["energized"]
        lines, viol, watch = {}, [], []
        for i, l in enumerate(g.branches):
            lid = l["id"]
            lv = []
            risk = float(r["line_risk"][0, i]) if on[i] else 0.0
            loading = float(r["loading"][0, i]) * 100.0 / cap_pct[i]  # % of the operator's cap (= rating if uncapped)
            temp = float(r["line_temp"][0, i])
            clr = float(r["line_clear"][0, i])
            if on[i]:
                if risk >= alarm and not l["cable"]:
                    msg = f"Line {lid}: fire risk {risk:.2f} >= {alarm:.2f} (gust {r['line_gust'][0, i]:.0f} m/s, RH {r['line_rh'][0, i]:.0f}%)"
                    if l["kv"] < SUBTRANSMISSION_MAX_KV:
                        lv.append(msg)
                    else:
                        watch.append(f"{msg.replace('Line ', 'Patrol ', 1)}: {l['kv']:.0f} kV import corridor kept energized")
                if clr < 3.0:
                    lv.append(f"Line {lid}: clearance {clr:.1f} m < 3.0 m (wind blowout toward trees)")
                if loading > 100:
                    if cap_pct[i] < 100:
                        lv.append(f"Line {lid}: loading {loading:.0f}% of the operator limit ({r['amps'][0, i]:.0f} A of {g.rating[i] * cap_pct[i] / 100:.0f} A, "
                                  f"capped at {cap_pct[i]:.0f}% of rating)")
                    else:
                        lv.append(f"Line {lid}: loading {loading:.0f}% > 100% ({r['amps'][0, i]:.0f} A of {g.rating[i]:.0f} A)")
                if temp > g.tmax[i]:
                    lv.append(f"Line {lid}: conductor {temp:.0f} C > max {g.tmax[i]:.0f} C")
            viol += lv
            lines[lid] = {"id": lid, "status": "open" if lid in opened else "closed", "energized": bool(on[i]),
                          "flow_mw": round(float(r["flow_mw"][0, i]), 1), "amps": round(float(r["amps"][0, i]), 0),
                          "loading_pct": round(loading, 1), "temp_c": round(temp, 1), "sag_m": round(float(r["line_sag"][0, i]), 2),
                          "clearance_m": round(min(clr, 99.0), 2), "blowout_m": 0.0, "swing_deg": 0.0, "fire_risk": D.shown_risk(risk),
                          "gust_ms": round(float(r["line_gust"][0, i]), 1), "rh_pct": round(float(r["line_rh"][0, i]), 0),
                          "cap_pct": float(cap_pct[i]), "violations": lv}
        P, load = g.injections(self.ldwp, self.sce, top, shed, imp)
        _, full = g.injections(self.ldwp, self.sce, g.base_topology())
        _, sourced, _, _ = g.ptdf(top)
        buses, backup, dark = {}, [], []
        for i, b in enumerate(g.buses):
            served = float(load[0, i]) if sourced[i] else 0.0
            uns = float(full[0, i]) - served
            info = self.buses[b["id"]]
            # a critical site goes to backup only if its substation loses supply (partial rotating shed skips its feeder)
            cut_off = not sourced[i] or not top.bus_on[i]
            ob = info["critical"] and cut_off
            if cut_off:
                dark.append(b["id"])
            if ob:
                backup.append(b["id"])
            buses[b["id"]] = {"id": b["id"], "served_mw": round(served, 1), "unserved_mw": round(max(uns, 0.0), 1),
                              "shed_mw": round(shed.get(b["id"], 0.0), 1), "flooded": False, "energized": bool(sourced[i] and top.bus_on[i]),
                              "deenergized": b["id"] in deen, "on_backup": ob}
        viol += errors
        tot_uns = sum(x["unserved_mw"] for x in buses.values())
        total_load = float(full.sum())
        if total_load > 0 and tot_uns > MAX_UNSERVED_FRACTION * total_load:
            viol.append(f"Blackout: {tot_uns:.0f} MW unserved is {tot_uns / total_load * 100:.0f}% of regional load (limit {MAX_UNSERVED_FRACTION * 100:.0f}%)")
        return {"lines": lines, "buses": buses, "gens": {},
                "totals": {"unserved_mw": round(tot_uns, 1), "customers_out": int(tot_uns * CUSTOMERS_PER_MW),
                           "critical_on_backup": backup, "violation_count": len(viol), "load_mw": round(float(full.sum()), 0),
                           # every building behind a cut-off substation, by kind (critical_on_backup lists substations only)
                           "buildings_out": B.outage_impact(dark)},
                "violations": viol, "n1_warnings": [], "watch": watch, "actions": actions or [], "is_safe": not viol,
                "region_index": round(float(r["region_index"][0]), 1)}

    def recompute(self):
        with self.lock:
            self.sim = self.simulate([])
            return self.sim

    def snapshot(self):
        return {"eid": self.eid, "hour": self.hour_idx, "opened": list(self.operator_opened)}

    def reports_view(self):
        return []

    def apply_actions(self, actions):
        with self.lock:
            for a in actions:
                if a["type"] == "open_line" and a["target"] not in self.operator_opened:
                    self.operator_opened.append(a["target"])
                elif a["type"] == "deenergize_lines":
                    for t in a.get("targets") or []:
                        if t not in self.operator_opened:
                            self.operator_opened.append(t)
                elif a["type"] == "close_line" and a["target"] in self.operator_opened:
                    self.operator_opened.remove(a["target"])
                elif a["type"] == "deenergize_bus" and a["target"] not in self.deenergized:
                    self.deenergized.append(a["target"])
                elif a["type"] == "shed_load":
                    self.shed[a["target"]] = self.shed.get(a["target"], 0.0) + float(a.get("mw", 0))
                elif a["type"] == "limit_line" and a.get("target") in self.lines:
                    self.caps[a["target"]] = float(a.get("pct", 100))
                elif a["type"] == "dispatch_gen":
                    imp_ids = {x["id"]: self.grid.bix[x["bus"]] for x in D.grid()["imports"]}
                    if a["target"] in imp_ids:
                        self.imp_override = {**getattr(self, "imp_override", {}), imp_ids[a["target"]]: float(a.get("mw", 0))}
            self.recompute()

    def view(self) -> dict:
        s = self.sim
        return {"event": self.eid, "t_utc": self.t_utc.isoformat(), "scenario": self.scenario, "opened": self.operator_opened,
                "deenergized": self.deenergized, "totals": s["totals"], "violations": s["violations"][:40],
                "n_violations": len(s["violations"]),
                "lines": {k: {"status": v["status"], "fire_risk": v["fire_risk"], "loading_pct": v["loading_pct"], "energized": v["energized"]}
                          for k, v in s["lines"].items()},
                "plan_status": self.plan_status, "bulletin": self.bulletin}

    def is_modified(self) -> bool:
        """True when this state differs from the recorded hour (Study conditions or operator changes), so facts must come
        from its own physics rather than the precomputed replay."""
        return bool(any((self.conditions or {}).values()) or self.operator_opened or self.deenergized or self.shed or self.caps
                    or getattr(self, "imp_override", None))

    def fire_lines(self, sim: dict | None = None, limit: int = 25):
        """Energized overhead lines at or above the fire alarm in `sim` (default: this state's own), riskiest first, in the
        replay's over_alarm row shape with the downwind cone for this hour's wind. Returns (rows, cone points, total count)."""
        from site_agent import greenery as G

        from . import consequence as C
        from . import trees as T

        sim = sim or self.sim
        g = self.grid
        L = [sim["lines"][b["id"]] for b in g.branches]
        hot = self.hot_points(sim)
        rows, pts = [], []
        for n, (k, lat, lon, wf) in enumerate(hot[:limit]):
            b = g.branches[k]
            pts.append((lat, lon, wf))
            row = {"id": b["id"], "name": b["name"], "kv": b["kv"], "zone": b["zone"], "risk": round(L[k]["fire_risk"], 3),
                   "gust": L[k]["gust_ms"], "rh": L[k]["rh_pct"], "downwind": C.downwind(lat, lon, wf)}
            row["greenery"] = G.line_greenery(b["id"])  # from cached aerial photos; None until the line has photos
            row["trees"] = T.line_trees(b["id"])  # trees near the wires, counted on Vexcel imagery; None until counted
            if n < CUT_IMPACT_LINES:
                row["if_cut"] = self.cut_impact(b["id"], sim.get("actions"))
            rows.append(row)
        return rows, pts, len(hot)

    def hot_points(self, sim: dict | None = None) -> list[tuple[int, float, float, float]]:
        """Every live overhead line at or above the fire alarm in `sim`, riskiest first, as
        (branch index, lat, lon, wind-from degrees) at the line's midpoint: where its downwind cone starts."""
        sim = sim or self.sim
        g = self.grid
        alarm = D.calibration()["fire"]["line_alarm"]
        L = [sim["lines"][b["id"]] for b in g.branches]
        hot = sorted([k for k, l in enumerate(L) if l["energized"] and l["fire_risk"] >= alarm and not g.branches[k]["cable"]],
                     key=lambda k: -L[k]["fire_risk"])
        wx = self.wx
        wdir = wx["wind_direction_10m"][self.hour_idx]
        out = []
        for k in hot:
            lat, lon = np.array(g.branches[k]["geometry"]).mean(0)[:2]
            wf = float(wdir[int(np.abs(wx["lats"] - lat).argmin()), int(np.abs(wx["lons"] - lon).argmin())])
            out.append((k, float(lat), float(lon), wf))
        return out

    def cut_impact(self, line_id: str, actions=None) -> dict:
        """What loses grid power if this one line is switched off, on top of the current switching and `actions`:
        buildings by kind with their triage score, and the modeled customers behind the newly cut-off substations.
        Topology only (no power flow), and cached, because the screen asks for the riskiest lines on every refresh."""
        key = (line_id, json.dumps(actions or [], sort_keys=True), tuple(self.operator_opened), tuple(self.deenergized))
        if key not in self._cut_cache:
            base = list(actions or [])
            newly = self._cut_off(base + [{"type": "open_line", "target": line_id}]) - self._cut_off(base)
            impact = B.outage_impact(sorted(newly))
            impact["customers"] = int(sum(self.buses[b]["load_mw"] for b in newly) * CUSTOMERS_PER_MW)
            self._cut_cache[key] = impact
        return self._cut_cache[key]

    def _cut_off(self, actions) -> set[str]:
        """Substations with no supply once `actions` are applied on top of the current switching. Topology only."""
        g = self.grid
        top = self._topology(actions)[0]
        sourced = g.ptdf(top)[1]
        return {b["id"] for i, b in enumerate(g.buses) if not sourced[i] or not top.bus_on[i]}

    def step_impacts(self, actions) -> list[dict]:
        """For each action in order: the buildings behind the substations that this step, and not an earlier one, cuts off."""
        out, before = [], self._cut_off([])
        for k in range(len(actions or [])):
            after = self._cut_off(actions[:k + 1])
            out.append(B.outage_impact(sorted(after - before)))
            before = after
        return out

    def weather_extremes(self) -> dict:
        """Worst gridded weather this hour, from this state's own (possibly Study-scaled) inputs."""
        wx, h = self.wx, self.hour_idx
        return {"max_gust": round(float(np.nanmax(wx["wind_gusts_10m"][h])), 1), "min_rh": round(float(np.nanmin(wx["relative_humidity_2m"][h])), 0),
                "max_temp": round(float(np.nanmax(wx["temperature_2m"][h])), 1)}

    def copy_for_planning(self):
        return copy.copy(self)

    # ------------------------------------------------------------------ Study mode
    def with_conditions(self, cond: dict | None) -> "LAState":
        """Copy of this hour with the model INPUTS changed (Study mode what-if); self is never mutated.
        gust_pct scales gusts and sustained wind, rh_pct scales relative humidity (clipped 1-100%), temp_c adds to air
        temperature, demand_pct scales both balancing-area demands. Every model and threshold runs unchanged."""
        c = {k: float((cond or {}).get(k) or 0.0) for k in CONDITION_KEYS}
        st = copy.copy(self)
        st.lock = threading.RLock()
        st.operator_opened, st.deenergized = list(self.operator_opened), list(self.deenergized)
        st.shed, st.caps = dict(self.shed), dict(self.caps)
        st.plans, st.plan_status, st.bulletin = {}, {}, None
        st._cut_cache = {}  # customers behind a cut depend on the study's demand
        st.conditions = c
        if any(c.values()):
            h = self.hour_idx
            wx = {k: v for k, v in self.wx.items()}
            wx["time"] = self.wx["time"][h:h + 1]
            for k in _WX_HOURLY:
                if k in self.wx:
                    wx[k] = np.array(self.wx[k][h:h + 1], dtype=float)
            fw = 1.0 + c["gust_pct"] / 100.0
            wx["wind_gusts_10m"] = wx["wind_gusts_10m"] * fw
            wx["wind_speed_10m"] = wx["wind_speed_10m"] * fw
            wx["relative_humidity_2m"] = np.clip(wx["relative_humidity_2m"] * (1.0 + c["rh_pct"] / 100.0), 1.0, 100.0)
            wx["temperature_2m"] = wx["temperature_2m"] + c["temp_c"]
            st.wx, st.hour_idx = wx, 0
            fd = max(0.0, 1.0 + c["demand_pct"] / 100.0)
            st.ldwp, st.sce = self.ldwp * fd, self.sce * fd
            st.buses = {k: {**v, "load_mw": v["load_mw"] * fd} for k, v in self.buses.items()}
            st.weather = st._zone_weather()
            parts = [f"{lbl} {c[k]:+g}{u}" for k, lbl, u in (("gust_pct", "wind", "%"), ("rh_pct", "humidity", "%"),
                                                              ("temp_c", "temperature", " C"), ("demand_pct", "demand", "%")) if c[k]]
            st.scenario = f"{self.scenario} [STUDY: {', '.join(parts)}]"
        st.sim = st.simulate([])
        return st
