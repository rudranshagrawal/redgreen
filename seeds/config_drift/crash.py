"""Reproduce the config-drift bug.

Deployed config sets DB_PORT=5433 (string), and the naive `< 1024` check
in build_dsn does str-vs-int comparison, which raises TypeError on py3.
"""

import os

from src.runtime.db import build_dsn


def main() -> None:
    os.environ["DB_PORT"] = "5433"
    dsn = build_dsn("svc", "hunter2", "payments")
    print("dsn:", dsn)


if __name__ == "__main__":
    main()
