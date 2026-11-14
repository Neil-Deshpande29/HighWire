"""Design rules at the agent boundary: tools are hour-safe (no hindsight), model-written plan text carries no numbers,
malformed plans never raise, one hospital count, and the site endpoint answers on the live forecast. All offline."""
import inspect
import json
import os
import re
import warnings

import pandas as pd
import pytest

os.environ["MOCK_PACE"] = "0"
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)

from backend.agents.brief import TRUST_CHECKS  # noqa: E402
from backend.agents.tools import JobLog, make_tools  # noqa: E402
from backend.la import data as D  # noqa: E402
from backend.la import engine as E  # noqa: E402
from backend.la import replay as RP  # noqa: E402
from backend.la.state import LAState  # noqa: E402

warnings.filterwarnings("ignore")

EID = "E3"
T_BEFORE = "2025-01-07T18:00:00+00:00"  # eight hours before the first ignition of this event
T_DURING = "2025-01-08T21:00:00+00:00"  # both fires are burning
T_PEAK = "2025-01-10T02:00:00+00:00"  # the event's outage peak: what a model might ask a tool for
FIRE_NAMES = re.compile(r"eaton|hurst|woolsey|saddleridge", re.I)
GENERIC_IGNITION = "remove the ignition source"  # the rule planner's reason for any shut-off; names no event
ISO_STAMP =re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?)?")
CONTEXT_KEYS = {"event", "t_utc", "t_local", "region_index", "region_alarm", "lines_over_alarm", "top_lines", "weather",
                "redflag_zones_active", "storm_damage", "customers_out_this_hour", "sources"}


@pytest.fixture(scope="module")
def grid():
    return E.LAGrid()


@pytest.fixture(scope="module")
def before(grid):
    return LAState(EID, T_BEFORE, grid=grid)


# ------------------------------------------------------------------ 1. no hindsight in any agent tool
def _tool_texts(state) -> dict[str, str]:
    """Call EVERY tool make_tools hands out, with the most leading arguments a model could send."""
    asked = {"line_id": D.grid()["branches"][0]["id"], "actions_json": "[]", "event_id": EID, "t": T_PEAK}
    out = {}
    for fn in make_tools(state, JobLog(), "fire_agent"):
        out[fn.__name__] = fn(**{k: asked[k] for k in inspect.signature(fn).parameters})
    return out


def _without_grid_place_names(text: str) -> str:
    """A substation that shares a word with a fire (there is a real Eaton Substation) is a place, not a fire."""
    for b in D.grid()["buses"]:
        if b.get("name") and FIRE_NAMES.search(b["name"]):
            text = text.replace(b["name"], "")
    return text


def _stamps(text: str) -> list[pd.Timestamp]:
    return [t if t.tzinfo else t.tz_localize("UTC") for t in map(pd.Timestamp, ISO_STAMP.findall(text))]


@pytest.mark.parametrize("t", [T_BEFORE, T_DURING])
def test_no_tool_names_a_fire_or_shows_the_future(grid, t):
    state = LAState(EID, t, grid=grid)
    texts = _tool_texts(state)
    assert len(texts) == 11
    for name, text in texts.items():
        low = text.lower().replace(GENERIC_IGNITION, "")
        assert not FIRE_NAMES.search(_without_grid_place_names(text)), name
        assert "ignit" not in low and "peak" not in low and "v5" not in low and "v4a" not in low, name
        late = [s for s in _stamps(text) if s > state.t_utc]
        assert not late, (name, late)


def test_no_tool_shows_the_outage_peak_before_it_happens(before):
    s = RP.series(EID)
    peaks = {str(int(max(x for x in s[k] if x is not None))) for k in ("la_out", "four_county_out")}
    for name, text in _tool_texts(before).items():
        assert not any(p in text for p in peaks), name


def test_event_context_is_this_hour_whatever_the_model_asks_for(before):
    ctx = json.loads(make_tools(before, JobLog(), "fire_agent", ["get_event_context"])[0]("E1", T_PEAK))
    assert set(ctx) == CONTEXT_KEYS
    h = RP.hour(EID, t=T_BEFORE)
    assert ctx["event"] == EID and pd.Timestamp(ctx["t_utc"]) == before.t_utc
    assert ctx["region_index"] == h["region_index"] and ctx["lines_over_alarm"] == h["n_over_alarm"]
    assert ctx["customers_out_this_hour"] == h["outages"]
    no_args = json.loads(make_tools(before, JobLog(), "fire_agent", ["get_event_context"])[0]())
    assert no_args == ctx  # a call without a time used to return hour 0 of the event


def test_validation_for_agents_is_the_briefs_trust_checks(before):
    val, storm = (json.loads(f()) for f in make_tools(before, JobLog(), "wind_agent", ["get_validation_summary", "get_storm_damage"]))
    assert {c["id"] for c in val} == set(TRUST_CHECKS)
    assert storm["validation"] and set(storm["validation"]) <= set(TRUST_CHECKS)
    assert storm["storm"]["index"] == RP.hour(EID, t=T_BEFORE)["storm"]["index"]


def test_hindsight_view_stays_for_people(before):
    hs = RP.event_hindsight(EID, T_BEFORE)
    assert {f["name"] for f in hs["fires"]} == {"Eaton Fire", "Hurst Fire"} and hs["validation_for_event"]["v5_cases"]
    assert CONTEXT_KEYS <= set(hs) and "Eaton" in hs["name"]
    same = CONTEXT_KEYS - {"sources"}
    assert {k: v for k, v in RP.hour_context(EID, T_BEFORE).items() if k in same} == {k: hs[k] for k in same}
