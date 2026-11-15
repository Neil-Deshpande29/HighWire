"""Text alerts: when code sends one, the de-duplication, the dry run, and the orchestrator's send_alert tool.
Twilio is always mocked or unset here: no test can send a real text."""
import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import notify as N  # noqa: E402
from backend import routes_la as R  # noqa: E402
from backend.agents.mock_planner import run_mock  # noqa: E402
from backend.agents.tools import JobLog  # noqa: E402
from backend.la import data as D  # noqa: E402
from backend.la.engine import LAGrid  # noqa: E402
from backend.la.state import LAState  # noqa: E402

T_WOOLSEY = "2018-11-08T19:00:00+00:00"


@pytest.fixture(scope="module")
def grid():
    return LAGrid()


@pytest.fixture
def woolsey(grid):
    return LAState("E1", T_WOOLSEY, grid=grid)


@pytest.fixture
def twilio(monkeypatch):
    """Twilio fully set up, with the sender replaced: records what would go out on the wire."""
    sent = []
    for name, value in {"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "token", "TWILIO_FROM_NUMBER": "+15005550006",
                        "ALERT_PHONE": "+15005550009"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(N, "_twilio_send", lambda message, to: sent.append((to, message)) or "SM1")
    monkeypatch.setattr(N, "SYNC", True)
    return sent


def test_without_a_from_number_everything_is_a_dry_run(monkeypatch):
    assert N.status()["dry_run"] and set(N.status()["missing"]) == {"TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "ALERT_PHONE"}
    monkeypatch.setattr(N, "_twilio_send", lambda *a: pytest.fail("a dry run must not send"))
    e = N.send("Rewire test", source="test")
    assert e["status"] == "dry run" and N.status()["alerts"][0]["message"] == "Rewire test"
    for name, value in {"TWILIO_ACCOUNT_SID": "ACtest", "TWILIO_AUTH_TOKEN": "token", "ALERT_PHONE": "+15005550009",
                        "TWILIO_FROM_NUMBER": "<TWILIO NUMBER, format +1XXXXXXXXXX>"}.items():
        monkeypatch.setenv(name, value)
    assert N.status()["dry_run"] and N.status()["missing"] == ["TWILIO_FROM_NUMBER"]  # a placeholder is not a number
    assert N.status()["to"] == "number ending 0009"  # the screen never shows the whole number


def test_code_triggers_text_once_per_line_and_condition(woolsey, twilio):
    alarm = D.calibration()["fire"]["line_alarm"]
    hot = N.very_high_fire(woolsey)
    assert hot and all(r["risk"] >= N.VERY_HIGH_RISK * alarm for r in hot)  # well over the alarm, not merely over it
    assert len(hot) < len([v for v in woolsey.sim["violations"] if "fire risk" in v])
    down = N.likely_out(woolsey)
    assert down and all(t["p_damage"] >= N.LIKELY_OUT for t in down)
    first = N.on_hour(woolsey)
    assert len(first) == min(len(hot), N.MAX_PER_HOUR) + min(len(down), N.MAX_PER_HOUR)
    assert [m for _, m in twilio] == [e["message"] for e in first] and all(to == "+15005550009" for to, _ in twilio)
    assert first[0]["message"].startswith("Rewire ALERT: ") and "very high fire risk, gusts" in first[0]["message"]
    assert all(e["status"] == "sent" or e["status"] == "sending" for e in first)
    assert N.on_hour(woolsey) == [] and N.on_hour(LAState("E1", "2018-11-08T20:00:00+00:00", grid=woolsey.grid)) == []  # same window: no repeat
    calm = LAState("E1", "2018-11-08T08:00:00+00:00", grid=woolsey.grid)
    assert N.very_high_fire(calm) == [] and N.likely_out(calm) == []


def test_approving_a_plan_that_turns_lines_off_sends_a_text(woolsey, twilio):
    plan = {"id": "P1", "actions": [{"type": "deenergize_lines", "target": "flagged", "targets": ["L5", "L220"]}], "result": {"customers_out": 1200}}
    e = N.on_approval(woolsey, plan)
    assert "approved switching off 2 lines" in e["message"] and "1,200 customers" in e["message"] and e["source"] == "code"
    assert N.on_approval(woolsey, plan) is None  # the same approval is not texted twice
    assert N.on_approval(woolsey, {"id": "P2", "actions": [{"type": "dispatch_gen", "target": "G1", "mw": 5}], "result": {}}) is None


def test_the_orchestrators_tool_shares_the_dedupe_with_the_code_path(woolsey, twilio):
    line = N.very_high_fire(woolsey)[0]["id"]
    log = JobLog()
    tool = N.alert_tool(woolsey, log)
    assert tool.name == "send_alert"
    said = tool.invoke({"message": f"Rewire ALERT: {line} has 9000 homes at 99% risk"})  # the model's numbers never go out
    assert said.startswith("alert ") and "9000" not in twilio[-1][1] and "very high fire risk" in twilio[-1][1]
    assert N.ALERTS[-1]["source"] == "agent"
    events = [e for e in log.entries if e.get("kind") == "tool" and e["meta"]["tool"] == "send_alert"]
    assert len(events) == 1 and events[0]["meta"]["line"] == line
    n = len(twilio)
    assert tool.invoke({"message": f"Rewire ALERT: {line} again"}) == "already alerted for this line" and len(twilio) == n
    fire = [e for e in N.on_hour(woolsey) if e["why"] == "very high fire risk"]
    assert len(fire) == min(len(N.very_high_fire(woolsey)), N.MAX_PER_HOUR) - 1 and N.fire_text(woolsey, N.very_high_fire(woolsey)[0]) not in [e["message"] for e in fire]
    # ^ the code path does not repeat the agent's text, and the agent's text counts toward the window's limit
    assert tool.invoke({"message": "Rewire ALERT: L1 looks a bit windy"}).startswith("not sent")  # only for very high risk


def test_the_rule_based_orchestrator_uses_the_tool_under_the_same_rule(woolsey, twilio):
    log = JobLog()
    run_mock(woolsey, log, pace=0)
    assert not [e for e in log.entries if e.get("kind") == "tool" and e["meta"].get("tool") == "send_alert"]  # not an Operate run
    woolsey.alerts_on = True
    log = JobLog()
    run_mock(woolsey, log, pace=0)
    calls = [e["meta"]["line"] for e in log.entries if e.get("kind") == "tool" and e["meta"].get("tool") == "send_alert"]
    assert calls and set(calls) == {r["id"] for r in N.very_high_fire(woolsey)[:N.MAX_PER_HOUR]} | {t["id"] for t in N.likely_out(woolsey)[:N.MAX_PER_HOUR]}
    assert {e["source"] for e in N.ALERTS} == {"agent"} and len(twilio) == len(N.ALERTS)


def test_alerts_are_off_unless_switched_on(monkeypatch, twilio):
    monkeypatch.delenv("ALERTS_ON", raising=False)
    assert N._on_at_start() is False  # a fresh server texts nobody
    monkeypatch.setenv("ALERTS_ON", "1")
    assert N._on_at_start() is True
    monkeypatch.setitem(N.STATE, "on", False)
    assert N.send("Rewire ALERT: anything") is None and twilio == [] and N.status()["alerts"] == [] and N.status()["on"] is False
    with pytest.raises(HTTPException):
        R.alerts_test()
    assert R.alerts_switch(R.AlertSwitchIn(on=True))["on"] is True and N.send("Rewire test", source="test")["status"] == "sent" and len(twilio) == 1


def test_the_switch_and_the_test_button(twilio):
    assert R.alerts_test()["alerts"][0]["source"] == "test" and len(twilio) == 1
    assert R.alerts_switch(R.AlertSwitchIn(on=False))["on"] is False
    assert N.send("Rewire ALERT: anything", key="k") is None and len(twilio) == 1  # off: nothing goes out, nothing is recorded
    with pytest.raises(HTTPException):
        R.alerts_test()
    assert R.alerts_switch(R.AlertSwitchIn(on=True))["on"] is True


def test_a_failed_send_is_recorded_without_the_error_text(monkeypatch, twilio):
    def boom(message, to):
        raise RuntimeError("401 Authenticate: token e7-secret")

    monkeypatch.setattr(N, "_twilio_send", boom)
    e = N.send("Rewire test", source="test", wait=True)
    assert e["status"] == "failed" and e["error"] == "RuntimeError" and "secret" not in str(N.status())


def test_on_a_twilio_trial_account_the_stock_alert_goes_out_and_the_entry_says_so(woolsey, monkeypatch, twilio):
    """A trial account may only send Twilio's stock templates. The phone still gets a text for the red watch, an
    approval or a test; per-line alerts are recorded but not sent, since copies of one stock text say nothing."""
    wire = []

    class Refused(Exception):
        code = N.TRIAL_ERROR

    def trial_send(message, to):
        wire.append(message)
        if not message.startswith("sms_"):
            raise Refused("Unable to create record: Invalid template name. Trial accounts can only use predefined SMS templates.")
        return "SM1"

    monkeypatch.setattr(N, "_twilio_send", trial_send)
    e = N.send("Rewire test: text alerts are working.", source="test")
    assert wire == ["Rewire test: text alerts are working.", N.TRIAL_TEMPLATE]  # our wording refused, the stock alert sent
    assert e["status"] == "sent" and e["note"] == N.TRIAL_NOTE and e["message"].startswith("Rewire test") and N.status()["trial"]
    from backend.la import watch as W

    red = N.on_red(woolsey, W.at("E1", T_WOOLSEY))
    assert red["status"] == "sent" and wire[2:] == [N.TRIAL_TEMPLATE]  # once known to be a trial, no wasted first attempt
    lines = N.on_hour(woolsey)
    assert lines and all(a["status"] == "not sent" and a["note"] == N.TRIAL_SKIP for a in lines) and len(wire) == 3

    def down(message, to):
        raise RuntimeError("network unreachable")

    monkeypatch.setattr(N, "_twilio_send", down)
    assert N.send("Rewire test again", source="test")["status"] == "failed"  # any other failure is still a failure
