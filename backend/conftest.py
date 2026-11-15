"""Shared test setup: tests never read or write this machine's runtime data."""
import pytest


@pytest.fixture(autouse=True)
def _no_local_feedback(monkeypatch, tmp_path):
    """Operator feedback is kept in a file so it survives a restart. Tests get an empty one of their own, so a
    rejection made while trying the app never changes what a test sees."""
    from backend.la import feedback as F

    monkeypatch.setattr(F, "FILE", tmp_path / "operator_feedback.json")


@pytest.fixture(autouse=True, scope="session")
def _no_local_costar(tmp_path_factory):
    """The CoStar kit is licensed and lives only on a machine that imported it. Tests run on the public building
    list alone, so their numbers are the same everywhere; tests that need CoStar rows write their own small file."""
    from backend.la import buildings as B

    real = B.COSTAR_FILE
    B.COSTAR_FILE = tmp_path_factory.mktemp("costar") / "buildings.json"
    B.clear_cache()
    yield
    B.COSTAR_FILE = real
    B.clear_cache()


@pytest.fixture(autouse=True)
def _no_paused_ai_team(monkeypatch):
    """A quota refusal pauses the AI team for hours (backend/agents/team.py). Each test starts with it free to run."""
    from backend.agents import team as T

    monkeypatch.setitem(T._PAUSE, "until", 0.0)


@pytest.fixture(autouse=True)
def _no_alerts_left_over():
    """Text alerts remember what they sent so a line is not alerted twice. Each test starts with nothing remembered,
    alerts switched on, and (see the repo-root conftest.py) no Twilio settings, so nothing is ever sent."""
    from backend import notify

    notify.reset()
    notify.STATE["on"] = True
    notify._trial["on"] = False
    yield
    notify.reset()
