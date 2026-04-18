"""Worker dispatcher.

Bug: `make_handlers` builds a list of closures in a for-loop and each
closure captures `config` by reference, not by value. After the loop
finishes, every handler uses the LAST config. If the configs have
different schemas (different keys), calling an early handler crashes
because it reaches into whichever config the loop ended on.
"""

from __future__ import annotations

from typing import Callable


def make_handlers(configs: list[dict]) -> list[Callable[[str], str]]:
    """Return one handler per config. Handler prefixes its message with config['name']."""
    handlers: list[Callable[[str], str]] = []
    for config in configs:
        # BUG: `config` is captured by reference. After the loop, every
        # lambda sees the final iteration's config — which may not even
        # have a 'name' key.
        handlers.append(lambda msg: f"[{config['name']}] {msg}")
    return handlers
