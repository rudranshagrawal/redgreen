# RedGreen — teammate runbook

You have git access only. Start here.

## 1. Install prereqs (macOS)

```bash
brew install just python@3.12 openjdk@21
# Docker Desktop → https://www.docker.com/products/docker-desktop/ (open once after install so the daemon is running)
```

Linux/Windows: you need Python 3.11+, Docker, JDK 21, and `just`. The plugin sandbox (`just plugin-run`) only works on a machine that can run JetBrains IDEs — fine on Linux, fiddly on Windows.

## 2. Clone + get secrets

```bash
git clone git@github.com:rudranshagrawal/redgreen.git
cd redgreen
```

**Ask Rudransh (Signal/WhatsApp) for `.env.local`** — it has the OpenAI, Nebius, and Supabase keys. Drop it at the repo root. Do not commit it (it's gitignored).

If you want your own Supabase project instead, create one and paste `supabase/schema.sql` then `schema_v2.sql` then `schema_v3.sql` into the SQL editor in that order, then put your own URL + service-role key in `.env.local`.

## 3. One-time setup

```bash
just bootstrap       # creates .venv, installs Python deps
just runner-build    # builds the Docker runner image (~2 min first time)
just check           # prints PASS/FAIL for env vars + API reachability
```

If `just check` shows anything other than "env ok" and all APIs reachable, stop and fix before proceeding. Common failures:

- **`docker info` fails** → open Docker Desktop.
- **`OpenAI returned 401`** → `.env.local` key is wrong or has a trailing newline.
- **`Supabase returned 404`** → schema hasn't been applied yet. Run the three SQL files in order.

## 4. Three ways to run it

### (a) One episode from the CLI — fastest smoke test

```bash
just seed null_guard
```

Runs the whole pipeline headless against `seeds/null_guard/`. Takes ~10–15s. You should see a "winner:" line at the end. Other seeds: `input_shape`, `async_race`, `config_drift`, `rate_limiter`.

### (b) Full demo — backend + plugin sandbox

Two terminals.

**Terminal 1 — backend:**
```bash
just backend-run
```

Leaves FastAPI listening on `127.0.0.1:8787`. Episodes stream through its stdout.

**Terminal 2 — plugin sandbox:**
```bash
just plugin-run
```

First run downloads PyCharm (~1 GB, ~3 min). A sandbox PyCharm window opens with RedGreen pre-loaded. In the sandbox:

1. `File → Open` → point at `~/rudy/coding-projects/redgreen/seeds/null_guard/` (or any seed)
2. If it asks for a Python interpreter, pick `/opt/homebrew/bin/python3` or anything 3.11+
3. Open `crash.py`
4. Click the bug icon (▶ Debug). Python raises → the RedGreen tool window pops up on the right showing the race live.
5. When the race finishes, a gutter inlay appears at the failing line — click it to apply the patch.

### (c) Leaderboard (optional, already deployed)

```
https://redgreen-leaderboard.vercel.app/
```

Reads the same Supabase tables the backend writes to. Every episode you run shows up here within a few seconds.

## 5. Where to look when something breaks

- **Backend logs** → terminal 1 above. Every phase prints a line (`gen`, `RED`, `CROSS`, `JUDGE`, `winner:`).
- **Plugin logs** → in the sandbox PyCharm: `Help → Show Log in Finder`. Look for lines starting with `[RedGreen]`.
- **Runner stdout** → captured into each agent row's `eliminated_reason` in Supabase; also visible in the plugin's detail pane when you click a row.
- **4-phase debug loop** → see the "When something breaks" section of `CLAUDE.md`.

## 6. Useful commands

```bash
just               # list all recipes
just verify        # full pre-commit gate (env + docker + seeds)
just seed <name>   # one episode from CLI
just seed-all      # run all 4 seeds, check each produces a winner
just demo          # 60s demo sequence headless
just web-deploy    # push web/ to Vercel prod
```

## 7. What NOT to touch without asking

- `contracts/schemas.py` — frozen wire format between plugin and backend. Changing it silently breaks both sides.
- `supabase/schema*.sql` — already applied to the shared Supabase. Don't re-run destructively.
- Anything under `../redgreen-skeleton/` — hackathon rule 7B, we cannot use that code.

See `CLAUDE.md` for the full working agreement, demo script, and non-goals.
