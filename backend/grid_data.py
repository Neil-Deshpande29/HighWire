"""Synthetic 10 km x 10 km region: ridge (west), valley (middle), coast (east).

Everything is hand-authored so the physics stays small and readable.
Coordinates are meters: x grows west -> east, y grows south -> north.
"""
from __future__ import annotations

import copy
import math

SIZE_M = 10_000.0
SEA_X = 9_000.0

RIDGE_MAX_X = 3_200.0  # x < this -> ridge
COAST_MIN_X = 7_200.0  # x > this -> coast


def _smoothstep(a: float, b: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def height(x: float, y: float) -> float:
    """Terrain elevation in meters. Ridge ~1200 m in the west, valley ~150 m, coast 0-5 m, sea past 9 km."""
    ridge = 1050.0 * math.exp(-(((x - 1000.0) / 1400.0) ** 2)) * (0.9 + 0.1 * math.sin(y / 1300.0))
    valley = 150.0 * (1.0 - _smoothstep(6800.0, 8200.0, x))
    coast = 4.0 * (1.0 - _smoothstep(8200.0, 9000.0, x))
    sea = -8.0 * _smoothstep(9000.0, 9600.0, x)
    undulation = 25.0 * math.sin(x / 650.0) * math.cos(y / 800.0) * (1.0 - _smoothstep(6500.0, 8000.0, x))
    return ridge + valley + coast + sea + undulation


def zone_of(x: float) -> str:
    if x < RIDGE_MAX_X:
        return "ridge"
    if x > COAST_MIN_X:
        return "coast"
    return "valley"


def heightmap(n: int = 64) -> dict:
    step = SIZE_M / (n - 1)
    rows = [[round(height(i * step, j * step), 2) for i in range(n)] for j in range(n)]
    return {"n": n, "size_m": SIZE_M, "sea_x_m": SEA_X, "heights": rows}  # heights[j][i] -> (x=i*step, y=j*step)


# --------------------------------------------------------------------------- buses
_BUS_ROWS = [
    # id,   name,              x,     y,    load, critical_type, backup_h, flood pad
    ("B1", "Ridge Hydro", 1200, 7000, 5, "none", 0, 3.0),
    ("B2", "Summit", 1500, 3000, 20, "none", 0, 3.0),
    ("B3", "Pine Hollow", 2400, 8600, 15, "none", 0, 3.0),
    ("B4", "Valley Power Plant", 4600, 5000, 10, "none", 0, 3.0),
    ("B5", "Valley Town", 5200, 7600, 45, "none", 0, 3.0),
    ("B6", "Water Treatment Plant", 4000, 2000, 15, "water", 12, 3.0),
    ("B7", "Mill District", 6200, 2800, 40, "none", 0, 3.0),
    ("B8", "Central", 6400, 5800, 35, "none", 0, 3.0),
    ("B9", "Harbor", 8800, 7400, 30, "none", 0, 1.5),
    ("B10", "Mercy Hospital", 8300, 4500, 20, "hospital", 48, 3.0),
    ("B11", "Bayside", 8850, 2000, 25, "none", 0, 1.5),
    ("B12", "Ridge Junction", 2200, 5000, 10, "none", 0, 3.0),
]

GENERATORS = {
    "G1": {"id": "G1", "name": "Valley Gas Plant", "bus": "B4", "gen_max_mw": 400.0, "dispatch_mw": 0.0, "slack": True},
    "G2": {"id": "G2", "name": "Ridge Hydro", "bus": "B1", "gen_max_mw": 60.0, "dispatch_mw": 25.0, "slack": False},
}


def _build_buses() -> dict:
    buses = {}
    for bid, name, x, y, load, ctype, backup, pad in _BUS_ROWS:
        buses[bid] = {
            "id": bid,
            "name": name,
            "x": float(x),
            "y": float(y),
            "zone": zone_of(x),
            "load_mw": float(load),
            "critical": ctype != "none",
            "critical_type": ctype,
            "backup_hours": float(backup),
            "elevation_m": round(height(x, y), 2),
            "flood_threshold_m": pad,
        }
    return buses


# --------------------------------------------------------------------------- lines
_LINE_ROWS = [
    # id, from, to, rating_amps, status
    ("R1", "B1", "B12", 450, "closed"),
    ("R2", "B12", "B2", 450, "closed"),
    ("R3", "B1", "B3", 450, "closed"),
    ("R4", "B2", "B6", 450, "closed"),
    ("V1", "B12", "B4", 500, "closed"),
    ("V2", "B3", "B5", 500, "closed"),
    ("V3", "B4", "B6", 500, "closed"),
    ("V4", "B4", "B8", 800, "closed"),
    ("V5", "B4", "B7", 550, "closed"),
    ("V6", "B5", "B8", 500, "closed"),
    ("V7", "B6", "B7", 450, "closed"),
    ("V8", "B5", "B9", 450, "open"),  # normally-open tie: Valley Town -> Harbor
    ("C1", "B8", "B9", 450, "closed"),
    ("C2", "B8", "B10", 450, "closed"),
    ("C3", "B7", "B11", 400, "closed"),
    ("C4", "B9", "B10", 400, "closed"),
    ("C5", "B10", "B11", 400, "closed"),
]

TIE_LINES = {"V8"}


def _build_lines(buses: dict) -> dict:
    lines = {}
    for lid, a, b, rating, status in _LINE_ROWS:
        ba, bb = buses[a], buses[b]
        length = math.hypot(bb["x"] - ba["x"], bb["y"] - ba["y"])
        mx, my = (ba["x"] + bb["x"]) / 2, (ba["y"] + bb["y"]) / 2
        lines[lid] = {
            "id": lid,
            "from_bus": a,
            "to_bus": b,
            "zone": zone_of(mx),
            "voltage_kv": 115.0,
            # V4 follows the river bend: longer route than the straight line, so higher impedance
            "reactance_pu": round(0.012 * length / 1000.0 * (1.5 if lid == "V4" else 1.0), 5),
            "length_m": round(length, 1),
            "span_m": 300.0,
            "rating_amps": float(rating),
            "max_temp_c": 75.0,
            "attach_height_m": 15.0,
            "sag_at_ref_m": 5.0,
            "status": status,
            "mid_x": mx,
            "mid_y": my,
            "mid_elevation_m": round(height(mx, my), 2),
            "tie": lid in TIE_LINES,
        }
    return lines


# --------------------------------------------------------------------------- trees
# Ridge lines get a tall tree close to the conductor on the downwind (east) side
# -> wind blowout violation, plus a shorter tree almost under the wire
# -> thermal sag violation in a heat wave.
STORM_WIND_DIR_DEG = 90.0  # wind blows toward the east (downslope off the ridge)


def _right_normal(line: dict, buses: dict) -> tuple[float, float, float, float]:
    a, b = buses[line["from_bus"]], buses[line["to_bus"]]
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    return ux, uy, uy, -ux  # (ux, uy, nx, ny)


def _build_trees(lines: dict, buses: dict) -> list:
    trees = []
    wx, wy = math.sin(math.radians(STORM_WIND_DIR_DEG)), math.cos(math.radians(STORM_WIND_DIR_DEG))
    for lid, ln in lines.items():
        ux, uy, nx, ny = _right_normal(ln, buses)
        downwind = 1.0 if (wx * nx + wy * ny) >= 0 else -1.0
        if lid == "R2":
            specs = [(-60, 4.0 * downwind, 13.0), (0, 1.0 * downwind, 6.2), (70, -6.0 * downwind, 11.0)]
        elif lid == "R3":
            specs = [(-60, 4.0 * downwind, 13.0), (0, 1.0 * downwind, 5.4), (70, -6.0 * downwind, 11.0)]
        elif lid == "R1":  # trimmed last season: tall trees a bit further out
            specs = [(-60, 5.6 * downwind, 13.0), (0, 1.0 * downwind, 5.4), (70, -6.0 * downwind, 11.0)]
        elif ln["zone"] == "ridge":
            specs = [(-50, 8.0, 10.0), (40, -9.0, 11.0)]
        elif ln["zone"] == "valley":
            specs = [(-40, 7.0, 12.0), (50, -8.0, 10.0)]
        else:
            specs = [(0, 9.0, 8.0)]
        for along, lateral, h in specs:
            px = ln["mid_x"] + ux * along
            py = ln["mid_y"] + uy * along
            trees.append(
                {
                    "line": lid,
                    "x": round(px + nx * lateral, 2),
                    "y": round(py + ny * lateral, 2),
                    "height_m": h,
                    "lateral_offset_m": lateral,  # + = right-hand side of the from->to direction
                }
            )
    return trees


def build_region() -> dict:
    buses = _build_buses()
    lines = _build_lines(buses)
    return {
        "buses": buses,
        "lines": lines,
        "gens": copy.deepcopy(GENERATORS),
        "trees": _build_trees(lines, buses),
    }
