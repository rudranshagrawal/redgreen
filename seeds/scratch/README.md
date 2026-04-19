# seeds/scratch

Manual-test fixtures. NOT part of the automated `just seed-all` harness —
that runs only the four real seeds (`null_guard/`, `input_shape/`, `async_race/`,
`config_drift/`), each with its own `EXPECTED.md`, `crash.py`, and `pytest.ini`.

Files here exist for specific plugin paths we wanted to verify by hand during
development:

- `name_syntax.py` — deliberate `SyntaxError` (missing colon on `def`). Used
  to exercise the plugin's PSI-based syntax-error fallback + the backend's
  single-model fast-path. Does NOT trigger the 4-agent race.
- `zero_division.py` — deterministic `ZeroDivisionError`. Used to exercise
  the router's `math_error` pick + the cross-validation filter + the quality
  judge. This is the canonical "don't just change 0 to 1" test case that
  motivated adding the judge layer.

To use manually: open `seeds/null_guard/` as a project in PyCharm (they
piggyback on that seed's `src/` + `tests/` + `pytest.ini` layout) and run
the file under Debug.
