"""In-memory GridState singleton. No database."""
from __future__ import annotations

import copy
import threading
import time

from . import grid_data, scenarios
from .physics import simulate


class GridState:
    def __init__(self):
        self.lock = threading.RLock()
        self.region = grid_data.build_region()
        self.reset()

    # ------------------------------------------------------------------ lifecycle
    def reset(self, scenario: str = "Normal"):
        with self.lock:
            self.buses = copy.deepcopy(self.region["buses"])
            self.lines = copy.deepcopy(self.region["lines"])
            self.gens = copy.deepcopy(self.region["gens"])
            self.trees = self.region["trees"]
            self.deenergized: list[str] = []
            self.shed: dict[str, float] = {}
            self.reports: list[dict] = []
            self.plans: dict[str, dict] = {}
            self.plan_status: dict[str, str] = {}
            self.bulletin: dict | None = None
            self.operator_opened: list[str] = []
            self.set_scenario(scenario, seed_reports=True)

    def set_scenario(self, name: str, seed_reports: bool = True):
        with self.lock:
            sc = scenarios.get(name)
            # A scenario is a fresh situation: switching state goes back to defaults.
            self.lines = copy.deepcopy(self.region["lines"])
            self.gens = copy.deepcopy(self.region["gens"])
            self.deenergized, self.shed, self.operator_opened = [], {}, []
            self.plans, self.plan_status, self.bulletin = {}, {}, None
            self.scenario = name
            self.weather = sc["weather"]
            self.load_multiplier = sc["load_multiplier"]
            self.recompute()
            self.reports = []
            if seed_reports:
                from .reports import seed_reports_for

                seed_reports_for(self)

    # ------------------------------------------------------------------ physics
    def snapshot(self) -> dict:
        return {
            "buses": self.buses,
            "lines": self.lines,
            "gens": self.gens,
            "trees": self.trees,
            "weather": self.weather,
            "load_multiplier": self.load_multiplier,
            "deenergized": self.deenergized,
            "shed": self.shed,
        }

    def recompute(self) -> dict:
        with self.lock:
            self.sim = simulate(self)
            self.sim_time = time.time()
            return self.sim

    # ------------------------------------------------------------------ mutations
    def toggle_line(self, lid: str) -> dict:
        with self.lock:
            ln = self.lines[lid]
            ln["status"] = "open" if ln["status"] == "closed" else "closed"
            if ln["status"] == "open" and lid not in self.operator_opened:
                self.operator_opened.append(lid)
            elif ln["status"] == "closed" and lid in self.operator_opened:
                self.operator_opened.remove(lid)
            self.recompute()
            return ln

    def apply_actions(self, actions: list[dict]):
        """Apply a plan's actions to the live grid (operator-approved only)."""
        with self.lock:
            for a in actions:
                t, target = a["type"], a["target"]
                if t == "open_line":
                    self.lines[target]["status"] = "open"
                    if target not in self.operator_opened:
                        self.operator_opened.append(target)
                elif t == "close_line":
                    self.lines[target]["status"] = "closed"
                    if target in self.operator_opened:
                        self.operator_opened.remove(target)
                elif t == "shed_load":
                    self.shed[target] = self.shed.get(target, 0.0) + float(a.get("mw", 0))
                elif t == "dispatch_gen":
                    g = self.gens[target]
                    g["dispatch_mw"] = min(g["gen_max_mw"], max(0.0, float(a.get("mw", 0))))
                elif t == "deenergize_bus":
                    if target not in self.deenergized:
                        self.deenergized.append(target)
            self.recompute()

    # ------------------------------------------------------------------ views
    def corroborated_lines(self) -> set[str]:
        """Lines the physics flags: any violation or high fire risk."""
        out = set()
        for lid, l in self.sim["lines"].items():
            if l["violations"] or l["fire_risk"] >= 0.5:
                out.add(lid)
        return out

    def reports_view(self) -> list[dict]:
        flagged = self.corroborated_lines()
        out = []
        for r in self.reports:
            r = dict(r)
            if r.get("line"):
                r["corroborated"] = r["line"] in flagged
            elif r.get("bus"):
                b = self.sim["buses"][r["bus"]]
                r["corroborated"] = bool(b["flooded"] or b["unserved_mw"] > 0)
            else:
                r["corroborated"] = False
            out.append(r)
        return out

    def view(self) -> dict:
        with self.lock:
            return {
                "scenario": self.scenario,
                "scenario_names": list(scenarios.SCENARIOS),
                "scenario_description": scenarios.SCENARIOS[self.scenario]["description"],
                "weather": self.weather,
                "load_multiplier": self.load_multiplier,
                "buses": list(self.buses.values()),
                "lines": list(self.lines.values()),
                "gens": list(self.gens.values()),
                "trees": self.trees,
                "deenergized": self.deenergized,
                "shed": self.shed,
                "sim": self.sim,
                "reports": self.reports_view(),
                "plan_status": self.plan_status,
                "bulletin": self.bulletin,
            }


STATE = GridState()
