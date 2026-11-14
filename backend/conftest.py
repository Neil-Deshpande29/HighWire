"""Shared test setup: tests never read or write this machine's runtime data."""
import pytest


@pytest.fixture(autouse=True)
def _no_local_feedback(monkeypatch, tmp_path):
    """Operator feedback is kept in a file so it survives a restart. Tests get an empty one of their own, so a
    rejection made while trying the app never changes what a test sees."""
    from backend.la import feedback as F

    monkeypatch.setattr(F, "FILE", tmp_path / "operator_feedback.json")
