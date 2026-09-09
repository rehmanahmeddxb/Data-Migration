"""Shared pytest fixtures.

The application factory is environment driven, so each test gets its own
throw-away SQLite file via ``APP_DB_PATH``.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def app_factory(tmp_path, monkeypatch):
    """Return a callable that builds a fresh app against *db_file*."""

    def _factory(db_file: Path | None = None, **env):
        db_file = db_file or (tmp_path / "test_ams.db")
        monkeypatch.setenv("APP_DB_PATH", str(db_file))
        monkeypatch.setenv("DB_HEALTH_SNAPSHOT_PATH", str(tmp_path / "health_snapshot.json"))
        monkeypatch.setenv("ALLOW_EMPTY_DB", "1")
        monkeypatch.setenv("BACKUP_EMBEDDED_SCHEDULER", "0")
        monkeypatch.setenv("AMS_SCHEMA_VERSION", "v44")
        monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
        monkeypatch.setenv("DEFAULT_ADMIN_USER", "Admin")
        monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "Admin@fbm12345")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        app_pkg = importlib.import_module("app")
        return app_pkg.create_app()

    return _factory


@pytest.fixture()
def app(app_factory):
    return app_factory()


def make_csrf_client(app):
    """Flask test client that attaches the session CSRF token automatically.

    The production app enforces session-bound CSRF on every mutating route;
    this wrapper mirrors a real browser by injecting the token into form
    posts.  Tests that explicitly exercise the CSRF gate should build their
    own raw client via ``app.test_client()``.
    """
    raw = app.test_client()

    class _ClientProxy:
        def __getattr__(self, name):
            return getattr(raw, name)

        def _csrf_token(self):
            with raw.session_transaction() as sess:
                token = sess.get("_csrf_token")
            if not token:
                token = "test-csrf-token"
                with raw.session_transaction() as sess:
                    sess["_csrf_token"] = token
            return token

        def post(self, *args, **kwargs):
            # JSON posts cannot also carry form ``data``.
            if kwargs.get("json") is not None:
                headers = dict(kwargs.get("headers") or {})
                if "X-CSRF-Token" not in headers and "X-CSRFToken" not in headers:
                    headers["X-CSRF-Token"] = self._csrf_token()
                    kwargs["headers"] = headers
                return raw.post(*args, **kwargs)
            data = kwargs.get("data")
            if data is None:
                data = {}
            if isinstance(data, dict) and "_csrf_token" not in data:
                data = dict(data)
                data["_csrf_token"] = self._csrf_token()
                kwargs["data"] = data
            return raw.post(*args, **kwargs)

    return _ClientProxy()


@pytest.fixture()
def client(app):
    return make_csrf_client(app)
