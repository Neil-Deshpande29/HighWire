"""LLM path of the co-pilot team, offline: a scripted fake chat model stands in for Gemini. The key is a dummy set
only inside each test and the model factory is patched, so nothing can reach the network."""
import json
import re
import threading
import uuid
import warnings
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.types import Command
from pydantic import Field

from backend import llm as LLM
from backend.agents import team as T
from backend.agents.tools import JobLog, simulate, summarize_result
from backend.la.state import LAState

warnings.filterwarnings("ignore")

SPECIALIST_TOOL = {"fire_agent": "get_grid_summary", "wind_agent": "get_storm_damage", "heat_agent": "get_weather",
                   "brownout_agent": "get_critical_sites"}
INVENTED = "risk 0.97 puts 123456 homes in the path"  # a specialist's made-up evidence; must never reach the log
VERIFIED = ("is_safe", "unserved_mw", "customers_out", "critical_on_backup", "n_violations")
HINDSIGHT_TOOLS = {"get_event_context", "get_validation_summary"}


def _txt(content) -> str:
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def _call(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:10]}", "type": "tool_call"}


class FakeGemini(BaseChatModel):
    """Scripted tool-calling model: it picks its next step from the message history, as a real run would unfold."""

    final: str = ""  # the coordinator's last message; "" means a well-formed answer built from the candidate actions
    hold: Any = None  # a threading.Event: every call waits for it and then fails, to act out a model that hangs
    bound: list = Field(default_factory=list)  # tool names handed to each agent

    @property
    def _llm_type(self) -> str:
        return "fake-gemini"

    def bind_tools(self, tools, **kwargs):
        self.bound.append([getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else t.__name__) for t in tools])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self.hold is not None:
            self.hold.wait(10)
            raise RuntimeError("fake model stopped")
        system = "\n".join(_txt(m.content) for m in messages if isinstance(m, SystemMessage))
        who = re.search(r"You are (\w+_agent) on a power-grid", system)
        msg = self._specialist(who.group(1), messages) if who else self._coordinator(messages)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _specialist(self, name: str, messages) -> AIMessage:
        tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
        if not tool_msgs:
            return AIMessage(content="", tool_calls=[_call(SPECIALIST_TOOL[name], {})])
        line = re.search(r'"(L\d+)"', _txt(tool_msgs[-1].content))
        return AIMessage(content=json.dumps({"agent": name, "findings": [
            {"target": line.group(1) if line else "grid", "issue": "flags this line", "evidence": INVENTED, "severity": 2}]}))

    def _coordinator(self, messages) -> AIMessage:
        done = {m.name: m for m in messages if isinstance(m, ToolMessage)}
        if "task" not in done:
            return AIMessage(content="", tool_calls=[
                _call("task", {"description": "Assess current conditions and return your findings JSON.", "subagent_type": a})
                for a in SPECIALIST_TOOL])
        if "get_candidate_actions" not in done:
            return AIMessage(content="", tool_calls=[_call("get_candidate_actions", {})])
        action = json.loads(_txt(done["get_candidate_actions"].content))[0]["action"]
        if "simulate_plan" not in done:
            return AIMessage(content="", tool_calls=[_call("simulate_plan", {"actions_json": json.dumps([action])})])
        return AIMessage(content=self.final or json.dumps({
            "situation": "Lines are over the fire alarm.",
            "plans": [{"id": "P1", "title": "Shut off the riskiest lines",
                       "steps": [{"action": action, "why": "over the fire alarm", "raised_by": ["fire_agent"]}],
                       "tradeoffs": "some load is lost", "assumptions": [],
                       "result": {"is_safe": True, "unserved_mw": -999, "customers_out": -999}}],
            "disagreements": [], "do_nothing": {"unserved_mw": -999}}))


@pytest.fixture(scope="module")
def state():
    return LAState("E3", "2025-01-08T21:00:00+00:00")


@pytest.fixture
def fake(monkeypatch):
    """A fresh fake model behind the app's one model factory, with a dummy key so the LLM path is taken."""
    model = FakeGemini()
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-not-a-real-key")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(LLM, "chat_model", lambda *a, **k: model)
    return model


def _is_fallback(out: dict, reason: str) -> bool:
    return out["mode"] == "mock" and reason in out["fallback_reason"] and bool(out["plans"])


def test_happy_path_uses_the_model_plans_with_physics_numbers(state, fake):
    log = JobLog()
    out = T.run_team(state, log)
    assert out["mode"] == "llm" and out["fallback_reason"] is None
    assert out["plans"] and out["do_nothing"]["unserved_mw"] != -999
    for p in out["plans"]:
        fresh = summarize_result(simulate(state, p["actions"]))
        assert {k: p["result"][k] for k in VERIFIED} == {k: fresh[k] for k in VERIFIED}
        assert p["result"]["verified_by"] == "physics engine"
    messages = [e["message"] for e in log.entries]
    assert not any("Command(" in m or "ToolMessage" in m for m in messages)
    assert not any("123456" in m or "0.97" in m for m in messages)
    by_agent = {e["agent"]: e["message"] for e in log.entries if e["message"].endswith("flags this line")}
    assert set(by_agent) == set(SPECIALIST_TOOL)
    assert re.fullmatch(r"L\d+: flags this line", by_agent["fire_agent"])


def test_no_agent_is_handed_a_hindsight_tool(state, fake):
    T.run_team(state, JobLog())
    assert fake.bound and not HINDSIGHT_TOOLS & {name for names in fake.bound for name in names}


def test_missing_json_falls_back(state, fake):
    fake.final = "I could not decide on a plan. Sorry."
    out = T.run_team(state, JobLog())
    assert _is_fallback(out, "no JSON")


def test_wrong_shaped_json_falls_back(state, fake):
    fake.final = json.dumps({"situation": "x", "plans": ["de-energize L1", "do nothing"]})
    log = JobLog()
    out = T.run_team(state, log)
    assert _is_fallback(out, "could not be used")
    assert sum("re-verified P1" in e["message"] for e in log.entries) == 1  # only the plan the operator is shown


@pytest.mark.parametrize("answer", [
    {"situation": "x"},
    {"plans": "none"},
    {"plans": {"P1": {"steps": []}}},
    {"plans": [{"title": "t", "steps": {"action": {"type": "open_line", "target": "L1"}}}]},
    {"plans": [{"title": "t", "steps": ["open L1"]}]},
    {"plans": [{"title": "t", "steps": [{"action": {"type": "shed_load", "target": "NOPE", "mw": "lots"}}]}]},
    {"plans": []},
])
def test_unusable_answers_fall_back_without_raising(state, fake, monkeypatch, answer):
    monkeypatch.setattr(T, "_run_llm", lambda state, log: answer)
    out = T.run_team(state, JobLog())
    assert out["mode"] == "mock" and out["fallback_reason"] and out["plans"]


def test_low_timeout_falls_back(state, fake, monkeypatch):
    monkeypatch.setenv("AGENT_TIMEOUT_S", "0.2")
    fake.hold = threading.Event()
    try:
        out = T.run_team(state, JobLog())
    finally:
        fake.hold.set()  # let the abandoned worker thread end
    assert _is_fallback(out, "timed out")


GOOGLE_DAILY_REFUSAL = ("Error calling model 'gemini-3.5-flash' (RESOURCE_EXHAUSTED): 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
                        "'You exceeded your current quota. Quota exceeded for metric: generate_content_free_tier_requests, limit: 20. "
                        "Please retry in 4h35m58.88s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'quotaId': "
                        "'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
                        "'retryDelay': '16558s'}]}}")


def test_a_used_up_quota_is_said_plainly_and_the_ai_is_not_asked_again_until_it_resets(state, fake, monkeypatch):
    monkeypatch.setitem(T._PAUSE, "until", 0.0)
    asked = []

    def refuse(state, log):
        asked.append(1)
        raise RuntimeError(GOOGLE_DAILY_REFUSAL)

    monkeypatch.setattr(T, "_run_llm", refuse)
    first = T.run_team(state, JobLog())
    assert _is_fallback(first, "Google's free daily limit for this key is used up") and "about 5 hours" in first["fallback_reason"]
    assert "RESOURCE_EXHAUSTED" not in first["fallback_reason"] and "{" not in first["fallback_reason"]
    again = T.run_team(state, JobLog())
    assert _is_fallback(again, "daily limit") and len(asked) == 1  # the second run did not wait on Google at all
    monkeypatch.setitem(T._PAUSE, "until", 0.0)  # the limit has reset
    T.run_team(state, JobLog())
    assert len(asked) == 2
    assert T.quota_refusal(RuntimeError("connection reset")) is None
    # the pause outlives a restart: a fresh process reads it back and does not wait on Google again
    saved = json.loads(T._PAUSE_FILE.read_text())
    assert saved["until"] == T._PAUSE["until"] > 0
    monkeypatch.setitem(T._PAUSE, "until", 0.0)  # what a restart does to the pause in memory
    T._load_pause()
    assert T._PAUSE["until"] == saved["until"] and _is_fallback(T.run_team(state, JobLog()), "daily limit") and len(asked) == 2
    monkeypatch.setitem(T._PAUSE, "until", 0.0)
    monkeypatch.setenv("AGENT_PLANNER", "rules")  # the setting that never asks the AI team at all
    chosen = T.run_team(state, JobLog())
    assert chosen["mode"] == "mock" and chosen["fallback_reason"] is None and len(asked) == 2
    monkeypatch.delenv("AGENT_PLANNER")
    assert T.quota_refusal(RuntimeError("429 RESOURCE_EXHAUSTED requests per minute 'retryDelay': '12s'")) == ("Google is limiting how fast this key may ask", 30.0)


def test_no_key_runs_the_rule_based_planner(state, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    out = T.run_team(state, lambda agent, message: None)
    assert out["mode"] == "mock" and out["fallback_reason"] is None


def _task_result(text: str) -> Command:
    """What the deepagents `task` tool returns: a Command wrapping the specialist's last message."""
    return Command(update={"messages": [ToolMessage(content=text, tool_call_id="call_1", name="task")]})


def _logged(text: str, description: str = "Assess current conditions.") -> list[tuple[str, str]]:
    lines = []
    cb = T._make_callback(lambda agent, message: lines.append((agent, message)))
    cb.on_tool_start({"name": "task"}, "", run_id="r1", inputs={"subagent_type": "fire_agent", "description": description})
    cb.on_tool_end(_task_result(text), run_id="r1")
    return lines


def test_callback_turns_a_command_into_clean_log_lines():
    findings = {"agent": "fire_agent", "findings": [
        {"target": "L220", "issue": "over the fire alarm", "evidence": "L220 red flag, brush fuel", "severity": 3},
        {"target": "L29", "issue": "over the fire alarm", "evidence": "fire_risk 0.32, 4000 homes", "severity": 2}]}
    assert _logged(json.dumps(findings)) == [
        ("coordinator", "-> fire_agent: Assess current conditions."),
        ("fire_agent", "L220: over the fire alarm (L220 red flag, brush fuel)"),
        ("fire_agent", "L29: over the fire alarm"),
    ]


def test_callback_withholds_model_text_that_states_numbers():
    findings = {"findings": [{"target": "42 lines", "issue": "ignites with 92% probability", "evidence": "three towers"}]}
    lines = _logged(json.dumps(findings), description="fire_agent saw 610,000 people downwind")
    assert lines == [("coordinator", "-> fire_agent"), ("fire_agent", "raised a finding (wording withheld: it stated numbers)")]
    assert _logged("Sorry, 17 things went wrong")[1] == ("fire_agent", "returned no readable findings")
    assert _logged(json.dumps({"agent": "fire_agent", "findings": []}))[1] == ("fire_agent", "no significant findings")


def test_prompts_ask_for_words_not_numbers_and_name_no_hindsight_tool():
    la_prompts = T.LA_COORDINATOR_PROMPT + " ".join(focus for focus, _ in T.LA_SUBAGENTS.values())
    assert not any(tool in la_prompts for tool in HINDSIGHT_TOOLS)
    assert not any(HINDSIGHT_TOOLS & set(tools) for _, tools in T.LA_SUBAGENTS.values())
    assert '"trust"' in T.LA_COORDINATOR_PROMPT
    assert "L867" not in la_prompts and "P(fault)" not in la_prompts and "not an ignition probability" in la_prompts
    assert "numbers copied" not in T.SUB_PROMPT and "do not write numbers" in T.SUB_PROMPT
    for prompt in (T.COORDINATOR_PROMPT, T.LA_COORDINATOR_PROMPT):
        assert "confidence" not in prompt and "the backend adds the verified numbers" in prompt
