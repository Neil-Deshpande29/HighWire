"""Guards in the validation checks, exercised on a five-line synthetic grid so the 15-minute validation run is not needed.
V8 ranks ignition lines among overhead lines only, so it must pick the scored line by the same rule as V5."""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validation"))

from checks import v8_forecast as V8  # noqa: E402

CAL = {"fire": {"line_alarm": 0.3}}
TIMES = pd.date_range("2030-01-01T00:00Z", periods=3, freq="h")
IGNITION = datetime(2030, 1, 1, 1, 40, tzinfo=timezone.utc)  # floors to hour 1


def _grid():
    """Five lines running north, one tower each: L0, L1, L2 and L4 overhead, L3 an underground cable.
    The ignition sits on L1's tower, so L1 is the nearest overhead line."""
    ids = [f"L{i}" for i in range(5)]
    return SimpleNamespace(branches=[{"id": i} for i in ids], lix={i: k for k, i in enumerate(ids)},
                           cable=np.array([False, False, False, True, False]), tb=np.arange(5),
                           tlat=34.0 + 0.01 * np.arange(5), tlon=np.full(5, -118.0))


def _hindcast():
    """Risk and gust both rise from L0 to L4 at the ignition hour, so each overhead line has a distinct rank."""
    risk = np.zeros((3, 5))
    risk[1] = [0.05, 0.10, 0.35, 0.90, 0.60]
    return {"line_risk": risk, "line_gust": risk * 30}


def _ranks(monkeypatch, **fire):
    grid = _grid()
    f = {"event": "EX", "name": "Synthetic ignition", "ignition_utc": IGNITION, "lat": float(grid.tlat[1]), "lon": -118.0, **fire}
    monkeypatch.setattr(V8.D, "fires", lambda: {"synthetic": f})
    return V8._ignition_ranks(grid, CAL, "EX", TIMES, _hindcast())


def test_v8_scores_the_named_line_when_it_is_overhead(monkeypatch):
    (rank,) = _ranks(monkeypatch, facility_line="L2")
    assert rank["line"] == "L2"  # named, not the nearest (L1)
    assert rank["model_top_pct"] == pytest.approx(50.0)  # second riskiest of the four overhead lines
    assert rank["line_risk"] == pytest.approx(0.35) and rank["above_line_alarm"] is True


@pytest.mark.parametrize("facility_line", ["L3", "L99", "", None])
def test_v8_falls_back_to_the_nearest_overhead_line(monkeypatch, facility_line):
    """A named cable has no rank among overhead lines: V8 used to raise IndexError where V5 falls back to the nearest line."""
    (rank,) = _ranks(monkeypatch, facility_line=facility_line)
    assert rank["line"] == "L1"
    assert rank["model_top_pct"] == pytest.approx(75.0)  # third riskiest of the four overhead lines
    assert rank["above_line_alarm"] is False


def test_v8_skips_other_events_and_ignitions_outside_the_window(monkeypatch):
    assert _ranks(monkeypatch, event="EY") == []
    assert _ranks(monkeypatch, ignition_utc=datetime(2030, 1, 2, tzinfo=timezone.utc)) == []
