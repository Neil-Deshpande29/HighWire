"""Rule-based planner on the real LA grid: it speaks only for the four LA agents, never returns the same plan twice,
reads the fire alarm from calibration and words the downwind count honestly. Offline: no key, no network."""
import copy
import re

import pytest

from backend.agents import mock_planner as MP
from backend.agents.tools import summarize_result
from backend.la import data as D
from backend.la import engine as E
from backend.la.state import LAState

LA_AGENTS = {"fire_agent", "wind_agent", "heat_agent", "brownout_agent"}
# "system" is the harness line that says which planner ran; it is not an agent voice
LA_VOICES = LA_AGENTS | {"coordinator", "physics", "system"}
TOY_ONLY_CLAIMS = ("N-1", "pads dry", "ground report", "floodwater", "equipment in water", "air density")
E3_T, E1_T = "2025-01-08T21:00:00+00:00", "2018-11-08T19:00:00+00:00"
OFF_TYPES = ("open_line", "deenergize_lines")


@pytest.fixture(scope="module")
def grid():
    return E.LAGrid()


def _run(st):
    log = []
    out = MP.run_mock(st, lambda agent, msg: log.append((agent, msg)), "test", pace=0)
    return st, out, log


@pytest.fixture(scope="module")
def e3(grid):
    return _run(LAState("E3", E3_T, grid=grid))


@pytest.fixture(scope="module")
def e1(grid):
    return _run(LAState("E1", E1_T, grid=grid))


def _off(plan) -> frozenset:
    """Lines a plan switches off."""
    out = set()
    for a in plan["actions"]:
        if a["type"] == "open_line":
            out.add(a["target"])
        elif a["type"] == "deenergize_lines":
            out |= set(a["targets"])
    return frozenset(out)


def _others(plan) -> frozenset:
    return frozenset((a["type"], a["target"], a.get("mw")) for a in plan["actions"] if a["type"] not in OFF_TYPES)


def _flagged(st, also_watch: bool = False) -> list[str]:
    """Sub-transmission lines over the fire alarm (the shut-off violations), worst first; optionally the bulk lines the
    state lists for patrol as well. The state's own verdict is the reference, not a re-comparison of rounded values."""
    found = st.sim["violations"] + (st.sim["watch"] if also_watch else [])
    ids = [m.group(1) for v in found if (m := re.match(r"(?:Line|Patrol) (\w+): fire risk", v))]
    return sorted(ids, key=lambda l: -st.sim["lines"][l]["fire_risk"])


def _assert_la_voices_only(out, log):
    assert {agent for agent, _ in log} <= LA_VOICES
    text = "\n".join(msg for _, msg in log)
    for claim in TOY_ONLY_CLAIMS:
        assert claim not in text, claim
    assert set(out["agent_findings"]) <= LA_AGENTS
    for p in out["plans"]:
        for s in p["steps"]:
            assert set(s["raised_by"]) <= LA_AGENTS, s["raised_by"]
    assert not any("cascade_agent" in d or "flood_agent" in d for d in out["disagreements"])


def _assert_plans_differ(st, plans):
    """No two plans switch the same set, and none is another plus lines that plan already leaves dead."""
    for i, p in enumerate(plans):
        for q in plans[i + 1:]:
            if _others(p) != _others(q):
                continue
            assert _off(p) != _off(q), "two plans switch the same lines"
            small, big = sorted((p, q), key=lambda x: len(_off(x)))
            if _off(small) < _off(big):
                lines = st.simulate(small["actions"])["lines"]
                assert any(lines[l]["energized"] for l in _off(big) - _off(small)), "a plan only adds lines that are already off"


def _assert_results_are_fresh_physics(st, plans):
    for p in plans:
        fresh = summarize_result(st.simulate(p["actions"]))
        assert {k: p["result"][k] for k in fresh} == fresh


def test_e3_log_speaks_only_for_the_la_team(e3):
    _, out, log = e3
    _assert_la_voices_only(out, log)
    assert {"fire_agent", "wind_agent", "heat_agent", "brownout_agent"} <= {agent for agent, _ in log}


def test_e3_never_returns_the_same_plan_twice(e3):
    st, out, log = e3
    assert out["plans"] and out["plans"][0]["result"]["is_safe"]
    _assert_plans_differ(st, out["plans"])
    _assert_results_are_fresh_physics(st, out["plans"])
    if len(out["plans"]) == 1:
        assert any(agent == "coordinator" and "no safe alternative" in msg for agent, msg in log)
    said = [msg for agent, msg in log if agent == "coordinator" and msg.startswith("done:")]
    assert said == [f"done: {len(out['plans'])} plan(s) ready for operator review"]


def test_e3_downwind_wording_is_honest(e3):
    st, _, log = e3
    fire = [msg for agent, msg in log if agent == "fire_agent"]
    assert not any(re.search(r"\b0 people", msg) for msg in fire), fire
    assert not any("sparks" in msg or "ignition risk" in msg for msg in fire), fire
    flagged = set(_flagged(st, also_watch=True))
    rows = [r for r in st.fire_lines(limit=len(st.lines))[0] if r["id"] in flagged]
    assert any(f"{len(flagged)} line(s) at or over the fire alarm" in msg for msg in fire), fire
    most = max(rows, key=lambda r: r["downwind"]["people"])
    assert most["downwind"]["people"] > 0
    assert any(most["id"] in msg and f"{most['downwind']['people']:,}" in msg for msg in fire), fire
    worst = rows[0]
    assert worst["downwind"]["people"] == 0, "this hour's riskiest line has an empty downwind cone"
    assert any(worst["id"] in msg and "no homes are counted" in msg for msg in fire), fire


def test_e1_full_shutoff_still_cuts_b506_and_has_no_twin(e1):
    st, out, log = e1
    _assert_la_voices_only(out, log)
    _assert_plans_differ(st, out["plans"])
    _assert_results_are_fresh_physics(st, out["plans"])
    n_flagged = len(_flagged(st, also_watch=True))
    assert n_flagged == 29 and any(a == "fire_agent" and m.startswith("29 line(s) at or over the fire alarm") for a, m in log)
    best = out["plans"][0]
    assert best["result"]["is_safe"] and not out["no_safe_option"]
    assert _off(best) == set(_flagged(st)) and len(_off(best)) == 15
    assert "B506" in best["result"]["critical_on_backup"]
    assert st.simulate(best["actions"])["buses"]["B506"]["served_mw"] == 0


def test_fire_alarm_is_read_from_calibration(grid, e3, monkeypatch):
    at_default = set(_flagged(e3[0], also_watch=True))
    cal = copy.deepcopy(D.calibration())
    cal["fire"]["line_alarm"] = 0.31
    monkeypatch.setattr(D, "calibration", lambda: cal)
    st, out, log = _run(LAState("E3", E3_T, grid=grid))
    over = set(_flagged(st, also_watch=True))
    assert over and over < at_default, "the raised alarm must drop some lines and keep others"
    assert {f["target"] for f in out["agent_findings"]["fire_agent"]} == over
    assert any(agent == "fire_agent" and f"{len(over)} line(s) at or over the fire alarm (0.31)" in msg for agent, msg in log)


def test_grouped_shutoff_step_carries_the_site_phrase(grid, monkeypatch):
    """The phrase comes from the worst line in the group that has a cached description, and names that line."""
    st = LAState("E3", E3_T, grid=grid)
    ids = _flagged(st)
    described = {ids[1]: "Dry grass runs up to a neighborhood.", ids[-1]: "Bare ground around the towers."}

    def cached_only(state, line_id):
        if line_id not in described:
            return None
        return {"line_summary": described[line_id], "photos": [], "disagreements": []}

    monkeypatch.setattr(MP, "_site", cached_only)
    cands = MP._Planner(st, lambda agent, msg: None, 0).candidates(st.sim)
    group = next(c for c in cands if c["action"]["type"] == "deenergize_lines" and set(c["action"]["targets"]) == set(ids))
    assert described[ids[1]] in group["why"] and ids[1] in group["why"]
    assert described[ids[-1]] not in group["why"]


def test_safe_subset_becomes_the_alternative(grid):
    """Selection logic only: the physics is relaxed so that one flagged line may stay on. The real hour has no such
    subset (see the E3 run above), so this is the one place the "found" branch is exercised."""
    st = LAState("E3", E3_T, grid=grid)
    ids = _flagged(st)

    def all_but(line):
        return [{"type": "deenergize_lines", "target": "test", "targets": [l for l in ids if l != line]}]

    keep = min(ids, key=lambda l: st.simulate(all_but(l))["totals"]["unserved_mw"])
    p = MP._Planner(st, lambda agent, msg: None, 0)
    full = next(c for c in p.candidates(st.sim) if set(c["action"].get("targets") or []) == set(ids))
    r_full = p.sim([full["action"]])
    assert st.simulate(all_but(keep))["totals"]["unserved_mw"] < r_full["totals"]["unserved_mw"]
    real = p.sim

    def relaxed(actions):
        r = real(actions)
        off = {l for a in actions for l in a.get("targets") or []}
        return r if keep in off else {**r, "violations": [], "is_safe": True}

    p.sim = relaxed
    combo, r = p.shutoff_alternative([full], r_full)
    assert set(combo[0]["action"]["targets"]) == set(ids) - {keep}
    assert keep in combo[0]["why"] and combo[0]["raised_by"] and set(combo[0]["raised_by"]) <= LA_AGENTS
    assert r["totals"]["unserved_mw"] < r_full["totals"]["unserved_mw"]
    p.sim = real
    assert p.shutoff_alternative([full], r_full) is None
