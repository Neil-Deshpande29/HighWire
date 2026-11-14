"""Trees near a line's wires, counted on Vexcel aerial imagery.

For each of a line's most fire-prone spots (the same spots the site agent looks at), Vexcel's detector is asked for
"tree" in a 300 m square, and the trees within CORRIDOR_M of the line are counted. A tree close to a wire in a wind
storm is how many power-line fires start, so a line with more trees near it is acted on first when risk is otherwise
equal. The count is context for the operator and the planner's ordering; it never changes the fire index.

Results are cached under data/processed/vexcel/trees/ (git-ignored: derived from licensed imagery). Reading the cache
never touches the network; `python -m backend.la.trees` fills it for the lines that reach the fire alarm.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

from . import data as D
from . import vexcel

DIR = D.P / "vexcel" / "trees"
CORRIDOR_M = 100.0  # the same corridor the vegetation share uses
SQUARE_HALF_M = 150.0  # each spot is looked at as a 300 m square
THRESHOLD = 0.5
MANY_FROM = 20  # trees near the wires from which a line is called out; a display threshold, not a model


def _metres_to_line(lat: float, lon: float, path: np.ndarray) -> float:
    """Shortest distance from a point to a line's path [(lat, lon), ...], on a flat local grid."""
    k = np.cos(np.radians(lat))
    p = np.array([0.0, 0.0])
    xy = np.c_[(path[:, 1] - lon) * 111_320 * k, (path[:, 0] - lat) * 110_540]
    a, b = xy[:-1], xy[1:]
    ab = b - a
    t = np.clip(((p - a) * ab).sum(1) / np.maximum((ab * ab).sum(1), 1e-9), 0, 1)
    return float(np.hypot(*(a + ab * t[:, None] - p).T).min())


def count(line_id: str, detect=vexcel.detect) -> dict:
    """Ask Vexcel about this line's spots and cache the answer. `detect` is replaceable for tests."""
    from site_agent import facts as SF

    path = np.array(SF.branch(line_id)["geometry"], dtype=float)[:, :2]
    seen, spots, uncovered = [], 0, 0
    for lat, lon in SF.line_points(line_id):
        try:
            found = detect(vexcel.square_wkt(lat, lon, SQUARE_HALF_M), "tree", THRESHOLD)
        except vexcel.NotCovered:
            uncovered += 1
            continue
        spots += 1
        for d in found:
            if _metres_to_line(d["lat"], d["lon"], path) <= CORRIDOR_M and not any(abs(d["lat"] - s[0]) < 2e-5 and abs(d["lon"] - s[1]) < 2e-5 for s in seen):
                seen.append((d["lat"], d["lon"]))  # squares can overlap: count a tree once
    out = {"line": line_id, "covered": spots > 0, "trees": len(seen), "spots": spots, "spots_not_covered": uncovered,
           "corridor_m": CORRIDOR_M, "source": vexcel.NAME, "counted_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{line_id}.json").write_text(json.dumps(out, indent=1))
    line_trees.cache_clear()
    return out


@lru_cache(maxsize=2048)
def line_trees(line_id: str) -> dict | None:
    """The cached count for a line, or None when it has not been counted or Vexcel does not cover it."""
    f = DIR / f"{line_id}.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text())
    if not d.get("covered"):
        return None
    return {"trees": d["trees"], "spots": d["spots"], "corridor_m": d["corridor_m"], "source": d["source"], "many": d["trees"] >= MANY_FROM}


def main(line_ids: list[str]) -> None:
    from site_agent import facts as SF

    ids = line_ids or SF.alarm_lines()
    for n, lid in enumerate(ids, 1):
        if (DIR / f"{lid}.json").exists():
            continue
        try:
            r = count(lid)
            print(f"[{n}/{len(ids)}] {lid}: {r['trees']} trees near the wires at {r['spots']} spots" + ("" if r["covered"] else " (outside Vexcel coverage)"))
        except Exception as e:  # noqa: BLE001  one bad line must not stop the rest
            print(f"[{n}/{len(ids)}] {lid}: failed: {str(e)[:120]}")


if __name__ == "__main__":
    main(sys.argv[1:])
