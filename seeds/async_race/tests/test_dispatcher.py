"""Happy-path tests — a single-config case happens to work even with the bug,
so it stays green on both unpatched and patched code."""

from workers.dispatcher import make_handlers


def test_single_handler_prefix():
    handlers = make_handlers([{"name": "auth"}])
    assert handlers[0]("login") == "[auth] login"
