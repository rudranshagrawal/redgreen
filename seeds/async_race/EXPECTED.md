# async_race seed

Classic late-binding closure bug in a worker dispatcher. Two configs with
different schemas expose the bug cleanly: the closure late-binds to the
LAST config, which lacks the 'name' key the handler reads.

## Expected exception

`KeyError: 'name'` at
`src/workers/dispatcher.py::make_handlers.<lambda>`.

## Expected winner

`async_race` hypothesis — the lens that reasons about ordering and
shared-reference bugs should spot "loop variable captured by closure"
and bind with `lambda msg, config=config:` or `functools.partial`.

## Anti-fix

Catching KeyError and swallowing it breaks the contract — the
first handler is supposed to return `"[auth] login"`, not nothing.
Only snapshotting the value at binding time actually fixes it.
