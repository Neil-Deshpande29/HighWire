"""LAState: the real-LA grid at one replay hour, exposing the same interface the Phase-1 planner/tools use
(lines, buses, weather, sim, simulate(actions), reports_view, ...). All numbers come from the deterministic engine.

Extra action type for PSPS-style plans:  {"type": "deenergize_lines", "target": "mountain lines over alarm", "targets": ["L12", ...]}
"""
from __future__ import annotations

import copy
import threading

import numpy as np
import pandas as pd

from . import data as D
from . import engine as E

CUSTOMERS_PER_MW = 500
# Utility-style safety policy (validation/DECISIONS.md, "Co-pilot safety policy"):
#  * a sub-transmission line (< 180 kV) above the fire alarm is a VIOLATION (de-energize it, PSPS style);
#  * a bulk 220-500 kV line above the alarm is a WATCH item (patrol/monitor): these are the import corridors that
#    feed the basin, and utilities keep them energized during PSPS (SCE's Jan 2025 PSPS cut 373 distribution circuits);
#  * overloads, conductor over max temperature, clearance < 3 m, and blackouts (> 10% of regional load unserved)
#    stay violations.
SUBTRANSMISSION_MAX_KV = 180.0
MAX_UNSERVED_FRACTION = 0.10


class LAState:
    def __init__(self, eid: str, t: str | None = None, grid: E.LAGrid | None = None):
        self.lock = threading.RLock()
        self.grid = grid or E.LAGrid()
        self.eid = eid
        self.wx = D.weather(eid)
        ev = D.events()[eid]
        hrs = E.hours_for(self.wx, ev["start_utc"], ev["end_utc"])
        times = pd.DatetimeIndex(self.wx["time"][hrs].astype("datetime64[ns]")).tz_localize("UTC")
        k = 0 if t is None else int(np.clip(np.searchsorted(times, pd.Timestamp(t).tz_convert("UTC")), 0, len(times) - 1))
        self.hour_idx = int(hrs[k])
        self.t_utc = times[k]
        ld, sc = E.event_demand(self.wx["time"][[self.hour_idx]])
        self.ldwp, self.sce = float(ld[0]), float(sc[0])
        g = self.grid
        self.scenario = f"LA replay {eid} @ {self.t_utc.tz_convert('America/Los_Angeles'):%Y-%m-%d %H:%M}"
        self.load_multiplier = 1.0
        self.operator_opened: list[str] = []
        self.deenergized: list[str] = []
        self.shed: dict[str, float] = {}
        self.reports: list[dict] = []
        self.plans, self.plan_status, self.bulletin = {}, {}, None
        self.gens: dict = {}
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
        self._last_imp = imp
        return top, shed, errors, opened, deen

    def simulate(self, actions=None) -> dict:
        g = self.grid
        top, shed, errors, opened, deen = self._topology(actions)
        imp = self._last_imp
        r = E.hindcast(g, self.wx, np.array([self.hour_idx]), self.ldwp, self.sce, top=top, shed=shed, imp_override=imp)
        cal = D.calibration()["fire"]
        alarm = cal["line_alarm"]
        on = r["energized"]
        lines, viol, watch = {}, [], []
        for i, l in enumerate(g.branches):
            lid = l["id"]
            lv = []
            risk = float(r["line_risk"][0, i]) if on[i] else 0.0
            loading = float(r["loading"][0, i])
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
                    lv.append(f"Line {lid}: loading {loading:.0f}% > 100% ({r['amps'][0, i]:.0f} A of {g.rating[i]:.0f} A)")
                if temp > g.tmax[i]:
                    lv.append(f"Line {lid}: conductor {temp:.0f} C > max {g.tmax[i]:.0f} C")
            viol += lv
            lines[lid] = {"id": lid, "status": "open" if lid in opened else "closed", "energized": bool(on[i]),
                          "flow_mw": round(float(r["flow_mw"][0, i]), 1), "amps": round(float(r["amps"][0, i]), 0),
                          "loading_pct": round(loading, 1), "temp_c": round(temp, 1), "sag_m": round(float(r["line_sag"][0, i]), 2),
                          "clearance_m": round(min(clr, 99.0), 2), "blowout_m": 0.0, "swing_deg": 0.0, "fire_risk": round(risk, 3),
                          "gust_ms": round(float(r["line_gust"][0, i]), 1), "violations": lv}
        P, load = g.injections(self.ldwp, self.sce, top, shed, imp)
        _, full = g.injections(self.ldwp, self.sce, g.base_topology())
        _, sourced, _, _ = g.ptdf(top)
        buses, backup = {}, []
        for i, b in enumerate(g.buses):
            served = float(load[0, i]) if sourced[i] else 0.0
            uns = float(full[0, i]) - served
            info = self.buses[b["id"]]
            # a critical site goes to backup only if its substation loses supply (partial rotating shed skips its feeder)
            ob = info["critical"] and (not sourced[i] or not top.bus_on[i])
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
                           "critical_on_backup": backup, "violation_count": len(viol), "load_mw": round(float(full.sum()), 0)},
                "violations": viol, "n1_warnings": [], "watch": watch, "actions": actions or [], "is_safe": not viol}

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

    def copy_for_planning(self):
        return copy.copy(self)
