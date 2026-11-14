"""Weather presets per zone. wind_dir_deg is the compass direction the wind blows TOWARD (90 = east)."""
from __future__ import annotations

import copy


def _z(t, wind, gust, hum, solar, flood=0.0, wdir=90.0):
    return {
        "air_temp_c": t,
        "wind_speed_ms": wind,
        "wind_dir_deg": wdir,
        "gust_ms": gust,
        "humidity_pct": hum,
        "solar_w_m2": solar,
        "flood_level_m": flood,
    }


SCENARIOS = {
    "Normal": {
        "description": "Mild weather everywhere. No flood.",
        "load_multiplier": 1.0,
        "weather": {
            "ridge": _z(18, 4.0, 7.0, 55, 600),
            "valley": _z(24, 3.0, 6.0, 50, 700),
            "coast": _z(21, 5.0, 8.0, 70, 650),
        },
    },
    "Heat Wave": {
        "description": "41 C in the valley and on the coast, almost no wind, strong sun. Load +35%.",
        "load_multiplier": 1.35,
        "weather": {
            "ridge": _z(34, 0.6, 2.0, 25, 1000),
            "valley": _z(41, 0.6, 2.0, 20, 1050),
            "coast": _z(41, 0.6, 2.0, 35, 1000),
        },
    },
    "Wind Storm": {
        "description": "Downslope wind storm: 25 m/s gusts on the ridge, 12 in the valley, 8% humidity (red flag).",
        "load_multiplier": 1.0,
        "weather": {
            "ridge": _z(16, 18.0, 25.0, 8, 500),
            "valley": _z(20, 8.0, 12.0, 8, 600),
            "coast": _z(18, 9.0, 14.0, 12, 600),
        },
    },
    "Coastal Flood": {
        "description": "Storm surge: 2.5 m of water at the coast, 15 m/s wind, heavy rain.",
        "load_multiplier": 1.0,
        "weather": {
            "ridge": _z(14, 8.0, 11.0, 95, 100),
            "valley": _z(16, 10.0, 13.0, 95, 100),
            "coast": _z(16, 15.0, 20.0, 98, 80, flood=2.5),
        },
    },
}


def get(name: str) -> dict:
    if name not in SCENARIOS:
        raise KeyError(f"Unknown scenario {name!r}. Options: {list(SCENARIOS)}")
    return copy.deepcopy(SCENARIOS[name])
