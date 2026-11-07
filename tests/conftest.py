"""Shared test setup."""

import pytest


@pytest.fixture(autouse=True)
def _testclient_host_is_allowed(monkeypatch):
    """FastAPI's TestClient sends Host: testserver; let the site answer to it in tests,
    the way it answers to 127.0.0.1 and localhost for real."""
    import decklens.web.app as app_module

    monkeypatch.setattr(app_module, "LOCAL_HOSTS", app_module.LOCAL_HOSTS | {"testserver"})
