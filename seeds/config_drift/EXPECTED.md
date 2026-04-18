# config_drift seed

`build_dsn` compares `port` against an int, but `port` is a str because
env vars are always strings. The old config loader pre-coerced; the new
one doesn't.

## Expected exception

`TypeError: '<' not supported between instances of 'str' and 'int'`
at `src/runtime/db.py::build_dsn`.

## Expected winner

`config_drift` hypothesis — the lens sees "env var read, then used as
a typed value without coercion". Fix: coerce with `int(os.environ.get("DB_PORT", DEFAULT_PORT))`,
or at least normalize at the top of `build_dsn`.

## Anti-fix

Catching TypeError and falling back to the default hides a real config
mistake. The fix must preserve the signal that someone set DB_PORT — it
just needs to respect that they set it as a string.
