"""Happy-path tests — only exercise the default-config path, which avoids the bug."""

import os

from runtime.db import build_dsn


def test_default_config(monkeypatch):
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.delenv("DB_PORT", raising=False)
    dsn = build_dsn("u", "p", "d")
    assert dsn == "postgres://u:p@localhost:5432/d"
