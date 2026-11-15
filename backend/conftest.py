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
