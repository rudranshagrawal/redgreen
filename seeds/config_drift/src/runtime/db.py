"""Database runtime config.

Bug: `build_dsn` reads `DB_PORT` from the environment but assumes it's
already an int — which it never is, because env vars are always strings.
In the old `.env`-managed stack this was pre-coerced upstream, but after
the config loader was swapped out, the raw string flows all the way here
and crashes when we do arithmetic on it.
"""

from __future__ import annotations

import os


DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5432


def build_dsn(user: str, password: str, database: str) -> str:
    """Return a postgres DSN using env-driven host/port."""
    host = os.environ.get("DB_HOST", DEFAULT_HOST)
    port = os.environ.get("DB_PORT", DEFAULT_PORT)

    # BUG: when DB_PORT is set (e.g. in test), `port` is a str.
    # The arithmetic here silently worked when DB_PORT was unset but
    # crashes the moment the test harness sets it.
    if port < 1024:
        raise ValueError("refusing to use a privileged port")

    return f"postgres://{user}:{password}@{host}:{port}/{database}"
