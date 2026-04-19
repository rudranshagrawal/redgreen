# RedGreen — Claude Working Agreement

## Mission

RedGreen is a JetBrains plugin that, when the debugger trips an exception, races 4 different models in parallel. Each returns `(failing test, patch, rationale)`. A Docker pytest runner is the referee: survivors must reproduce the bug RED, then flip it GREEN. The winning patch surfaces as a gutter suggestion. Every episode is logged to Supabase; a per-codebase leaderboard reweights the agent pool so later episodes pick the right model first.

Built at the JetBrains Codex Hackathon 2026-04-18 / 2026-04-19. Submission due Sunday 12:00 PM.

## Non-goals (locked)

- ❌ Chat UI / "ask the AI about your code" — trigger is the debugger, never a chat box.
- ❌ Web dashboard where users paste a stacktrace — kills the IDE-native frame.
- ❌ Mocks for the runner — pytest in Docker or it doesn't ship.
- ❌ Settings UI, persistent config, login, onboarding.
- ❌ More than 4 model slots.
- ❌ Generic multi-file refactor or ticket-to-PR — stay Track 2.
- ❌ Any code copied from `../redgreen-skeleton/` — hackathon rule 7B.

## The 60-second demo script (this IS the spec)

The one-sentence pitch to land first: **"Every patch has to survive the
runner, pass the other agents' tests, *not break the repo's own existing
tests*, and then win code review — from another LLM trained on what makes
a fix idiomatic."** Four layers of defense:
**runner → peers → regression → review.** Emphasize them as the beats
unfold.

Every file in this repo earns its place by serving this demo:

1. (0:00) Open PyCharm with `seeds/null_guard/`. RedGreen plugin visible in sidebar.
2. (0:05) Debug → exception: `TypeError: refund_amount must not be None`.
3. (0:08) Tool window: "RedGreen racing 4 agents..." — 4 lanes populate from the router.
4. (0:14) **This is the money beat.** As the race resolves, the phase column
   shows the four layers doing their job:
   - One row: `GREEN ✗ · 1/4 peer (hacked literal?)` — the runner said
     "your patch compiles" but peers called it out as a hack. Point at it.
   - A second row: `GREEN ✓ · 3/4 peer · REGRESSION ✗ broke 1 existing test(s)` —
     passed peers' tests but broke a happy-path test already in the repo.
     Say: "CI would have caught this. We caught it in 2 seconds."
   - Winner row: `🏆 WINNER · GREEN ✓ · 4/4 peer · 2/2 regression`.
   - Below the table: `[Judge] Candidate X addresses the cause rather than
     silencing the symptom; matches the project's RefundError convention.`
   Say out loud: "Runner caught syntax. Peers caught hacks. Regression caught
   side-effects. Judge caught style."
5. (0:22) Gutter inlay appears at the failing line: `⚡ RedGreen: fix ready · click`.
6. (0:26) Click the inlay. Patch applied. Test file created.
7. (0:30) Cut to Vercel leaderboard: hero stats, the "How it works" strip,
   the per-codebase block showing `predicts null_guard · 91% confidence`.
   Every row is a real episode from tonight's pre-seed.
8. (0:38) Cut back. Second bug on the same repo. Console line flashes:
   `[feedback] null_guard←gpt-5-mini (10W) | ...`. Say: "It read the
   leaderboard. It's picking the model that won here before." Race resolves
   in half the time because the priors were right.
9. (0:50) End card: `github.com/rudranshagrawal/redgreen · redgreen-leaderboard.vercel.app`.

If a change doesn't serve this demo, it doesn't ship this weekend.

### Q&A prep (memorize one-liners)

- **"Why only 4 agents out of 12?"** — "Latency and API cost. The router's
  score is strong enough that beyond the top 4 we don't see marginal wins."
- **"How does it learn?"** — "Every episode's winner writes to a `(repo_hash,
  agent, model)` table. The next episode reads it and biases model assignments
  toward historical winners on that codebase. Cold start falls back to random."
- **"Why four gates instead of one?"** — "Runner proves the patch compiles
  and passes a test. Cross-val proves it's robust to other agents' tests.
  Regression proves it didn't break anything else in the repo. Judge proves
  it's idiomatic. Each layer filters a different failure mode — the
  regression gate specifically is what stops a 'fix' that silently breaks
  an unrelated feature from ever reaching the gutter."
- **"What stops a model from writing a test that only its own patch passes?"**
  — "Cross-val. Every patch runs against every agent's tests combined.
  A patch that only satisfies its own test scores 1/4 and loses."
- **"Is the judge cheating? LLMs judging LLMs?"** — "The judge isn't
  deciding correctness — the runner already did that. The judge only
  breaks ties on *idiomaticness*, using general code-review principles.
  The referee (pytest in Docker) is deterministic; the judge is flavor."

## Three wire contracts (frozen — see `contracts/schemas.py`)

```python
# Plugin -> Backend
AnalyzeRequest:  stacktrace, locals_json, frame_file, frame_line,
                 frame_source, repo_hash, repo_snapshot_path
AnalyzeResponse: episode_id

# Backend -> Runner (internal)
RunRequest:  episode_id, agent, test_code,
             patch_unified_diff (Optional), repo_snapshot_path
RunResponse: status ("RED"|"GREEN"|"REGRESSION_FAILED"|"ERROR"), stdout, duration_ms

# Plugin <- Backend (poll)
StatusResponse: episode_id, state ("racing"|"completed"|"no_winner"),
                agents [AgentResult], winner (Optional), leaderboard_row (Optional)
```

Plugin and backend never block on each other — both implement to these schemas.

## Repo map + workstream ownership

- `contracts/` — shared. Edited once (M0) and then frozen.
- `backend/` — FastAPI orchestrator. Fan-out to providers, rank survivors, write episode. Plugin-agnostic.
- `runner/` — Docker pytest sandbox. Stateless. Runs RED gate then GREEN gate.
- `plugin/` — Kotlin JetBrains plugin. XDebugSessionListener → HTTP → tool window → gutter.
- `web/` — Next.js leaderboard on Vercel. Read-only view on Supabase.
- `seeds/{null_guard,input_shape,async_race,config_drift}/` — one buggy repo per hypothesis type. Each must RED→GREEN reliably under `just test-seed <name>`.
- `supabase/schema.sql` — `episodes`, `agents` rows, `leaderboard` view.
- `scripts/` — dev, demo, seed runners.

Plugin code and backend code only meet at `contracts/`. Never import across the boundary.

## Dev commands (justfile)

- `just check` — env loaded, Supabase + Codex + Nebius reachable.
- `just dev` — backend + runner image + web dev server.
- `just seed <name>` — one episode end-to-end from CLI (no plugin).
- `just seed-all` — verify all 4 seeds RED→GREEN.
- `just test-seed <name>` — single-seed smoke test in the runner.
- `just demo` — scripted 60-second demo sequence headless.
- `just runner-build` — build the Docker runner image.
- `just plugin-run` — `gradle runIde` sandbox.
- `just web-deploy` — `vercel --prod` from `web/`.

## Sponsor integration points

| Sponsor | Role | Status |
|---------|------|--------|
| Codex (OpenAI) | Model slot 1 (GPT-5 Codex) | load-bearing |
| Nebius Token Factory | Model slots 2–4 | load-bearing |
| Supabase | episodes + leaderboard storage | load-bearing |
| Docker | referee (pytest sandbox) | load-bearing |
| Vercel | leaderboard web host | load-bearing |
| AuthZed | PR authorization check | stretch (M6) |
| BKey | Face ID approval on deny | stretch (M6) |
| Clerk | — | cut (no auth in demo) |

## Hard rules

1. **No mocks for the runner.** Ever. Fake green checkmarks in the demo are disqualifying.
2. **No code reused from `../redgreen-skeleton/`.** That repo is design reference only. Commit history starting 2026-04-18 is our compliance proof.
3. **One component per edit loop.** Finish backend happy path before touching plugin. Finish plugin before polishing web.
4. **Trace-before-fix.** Read the stacktrace and the referee output before editing anything.
5. **No destructive git** (`rm -rf`, `--force`, `reset --hard`) without explicit user approval.
6. **Every new bug type needs a seed repo that RED→GREENs reliably** under `just test-seed`.
7. **Freeze contracts at M0.** If a schema change is truly needed, bump a version and update both sides in one commit.
8. **Do not start the dev server / runner / plugin sandbox unless asked.** Long-running processes should be intentional.
9. **Failing test first.** Before writing or changing orchestrator / provider / runner logic, write or update the test or seed that would catch the failure. The whole product is a TDD loop — we eat our own dog food.
10. **`just verify` must pass before every commit that changes backend/runner/plugin code.** If it doesn't pass, the commit doesn't land.

## When something breaks — 4-phase debug loop

Applies when *building* RedGreen stalls (not when the product detects a user's bug — that's the runner's job). Adapted from ECC agent-introspection. Use before asking the user for help.

**Phase 1 — Capture** (write it down, don't just stew):
- What was the goal? Which milestone + sub-task?
- Exact error / unexpected output (paste verbatim).
- Last tool call that worked. Last tool call that failed.
- Environment assumptions that could be wrong (cwd, docker state, env vars loaded, supabase reachable).

**Phase 2 — Diagnose** (pattern-match):

| Symptom | Likely cause | First check |
|---------|--------------|-------------|
| Same command retried, same failure | Logic error, not env | Read the stacktrace, not the last line |
| `ECONNREFUSED` to Docker | Daemon not running | `docker info` |
| `401/403` from OpenAI/Nebius/Supabase | Wrong env var loaded | `echo ${VAR}` from the venv's shell |
| Runner returns ERROR with "patch apply failed" | Diff format the runner doesn't support | Look at the generated diff; maybe regen or loosen parser |
| Runner returns ERROR on RED gate | Generated test doesn't reproduce | Inspect `test_code`; usually prompt issue, not runner issue |
| pytest passes when it should fail | Test is too lenient (`assert True`) | Harden the RED gate assertion in the prompt |
| Supabase insert silently drops rows | Enum mismatch or RLS | Check payload vs `schema.sql`; re-run `just verify` |

**Phase 3 — Contain**: smallest reversible action. Don't change two things at once. If unsure, back out the change first, confirm green, then retry one thing.

**Phase 4 — Introspect**: before calling the bug fixed, check: did we fix the right thing, or just make the error go away? Run `just verify` again. Re-read the original capture.

## Context compaction plan

Long hackathon sessions → context gets polluted. Compact at these boundaries, NOT mid-implementation:

- After M1 green → compact (drop exploration, keep CLAUDE.md + contracts).
- After M2 green → compact.
- After M3 green (or kill-switch fires) → compact.
- Before recording the demo (M7) → compact and reload the demo script.

Do NOT compact mid-implementation — losing variable names and open file paths is expensive. If the user is not sure, defer.

## Fallback decision tree (M3 kill-switch)

Hour 8 (Saturday 11 PM) gate: if the JetBrains plugin does not have the debugger exception listener firing end-to-end and POSTing to the backend, pivot.

**Pivot target:** MCP server (reuse patterns from `../trana-sprint-board/mcp/server.ts`). Exposes `analyze_exception` as an MCP tool. Demo recorded in Cursor / Claude Desktop instead of PyCharm.

**What survives the pivot:** everything in `backend/`, `runner/`, `web/`, `seeds/`, `supabase/`, `contracts/`. Only `plugin/` is discarded. Cost ≈ 30 minutes.

**What gets cut if we pivot:** the "IDE Reimagined" framing slides slightly — we still have the IDE-surface story because MCP lives inside the editor, but the gutter-suggestion Tab-to-apply demo beat becomes a tool-call response instead.

## Milestones (21 hours from 2026-04-18 15:00)

- M0 (h0–1): repo init + contracts frozen.
- M1 (h1–3): backend happy path, 1 model, 1 seed.
- M2 (h3–5): fan-out to 4 models, all seeds reproduce.
- M3 (h5–8): plugin end-to-end **← kill-switch at h8**.
- M4 (h8–10): leaderboard + reweighting + Vercel deploy.
- M5 (h10–12): polish round 1, seeds reliable.
- M6 (h12–15): AuthZed + BKey (stretch, only if M0–M5 green).
- M7 (h15–18): record demo video.
- M8 (h18–20): submission + pitch.
- M9 (h20–21): buffer / Q&A rehearsal.

## A note for Claude

Keep updates short. Before each milestone, re-read this file's non-goals. If I ask for something that violates a non-goal, push back first.
