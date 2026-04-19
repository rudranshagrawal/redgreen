# RedGreen — 60-second demo shooting script

One-page, hit-record-ready. Assumes you've done pre-flight (below).
Screen names match what you see on your display.

---

## Pre-flight (10 minutes before recording)

Three terminals + one browser, arranged like this:

```
┌─────────────────────┬──────────────────────────────────┐
│  BROWSER            │  PyCharm SANDBOX                 │
│  leaderboard tab    │  seeds/null_guard/ open          │
│  (hidden behind)    │  RedGreen tool window ON (right) │
│                     │  crash.py open in editor         │
├─────────────────────┴──────────────────────────────────┤
│  TERMINAL · backend uvicorn (leave visible bottom-left)│
└─────────────────────────────────────────────────────────┘
```

Pre-flight commands — run in order, confirm each before the next:

```bash
# 1. clean state
cd ~/rudy/coding-projects/redgreen
git status                                # "working tree clean"
git checkout -- seeds/null_guard/src/payments/refund.py
rm -f seeds/null_guard/tests/test_redgreen_generated.py

# 2. warm the stack (so cold-start isn't in your video)
bash scripts/verify.sh                     # 12/12 PASS
.venv/bin/python -m backend.orchestrator --seed null_guard --cli
#  ↑ pre-seeds Supabase with a winner so leaderboard isn't empty;
#  pre-warms Docker + API connections. Second run is ~30% faster.
git checkout -- seeds/null_guard/         # reset the seed again

# 3. start the backend (new terminal, leave running)
source .venv/bin/activate
set -a && . ./.env.local && set +a
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8787 --reload
#  wait for "Application startup complete"

# 4. launch the plugin sandbox (new terminal)
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
cd plugin && ./gradlew runIde
#  sandbox PyCharm opens → File → Open → seeds/null_guard/
#  (NOT seeds/ — one specific seed as the project root)
#  open crash.py in the editor
#  RedGreen tool window should be visible on the right; if not,
#  View → Tool Windows → RedGreen
```

Pre-open the browser tab: `https://redgreen-leaderboard.vercel.app/`.
Reload it once so the data's fresh.

Sanity test: hit ▶ Debug on `crash.py` ONCE before recording. Watch the
race go. Hit Apply. Confirm the gutter inlay fires. Close the file,
`git checkout -- seeds/null_guard/`, `rm -f tests/test_redgreen_generated.py`.
Now you know your state is clean and your stack is warm.

---

## Shooting script

Total run-time target: **~60s live**. The race itself takes ~28-33s —
plan to edit out the slower middle section in post, or start recording
just before the race resolves.

| Time   | Screen                      | Action                                                                                  | Voiceover                                                                                                            |
|--------|-----------------------------|-----------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| 0:00   | PyCharm sandbox             | Project tree visible · `crash.py` in the editor · RedGreen tool window **empty** on right | "This is a Python project with a bug."                                                                               |
| 0:03   | PyCharm editor              | Click the **▶ Debug** icon (top-right, not Run)                                         | "I hit Debug."                                                                                                       |
| 0:05   | PyCharm Debug panel         | `TypeError: unsupported operand type(s) for *: 'NoneType' and 'decimal.Decimal'` appears | "Python raises an exception."                                                                                        |
| 0:06   | RedGreen tool window        | Window auto-fills with 4 rows · subtitle reads `Phase 1 · models generating · 0/4 done` | "The RedGreen plugin fires — no button, no chat, just the debugger tripping. Four models race."                      |
| 0:10   | RedGreen tool window        | Subtitle ticking: `Phase 1 · 2/4 done · 14s` · rows filling with "RED ✓"                | "Each model proposes a failing test and a patch. Different hypothesis lenses — is this a null bug? A shape error? A race?" |
| 0:15   | RedGreen tool window        | Subtitle: `Phase 2 · peer cross-validation · 3/4 done` · peer counts appear              | "Then every patch is run against every other agent's tests. Hacks that only pass their own test die here."           |
| 0:18   | RedGreen tool window        | Subtitle: `Phase 3 · regression gate` · `7/7 regression` appears on survivors            | "**Then the regression gate** — the patch has to pass the repo's own existing tests. Fix the bug but break an unrelated feature? You're out. CI would have caught it. We caught it in 2 seconds." |
| 0:22   | RedGreen tool window        | Subtitle: `Phase 4 · judge reviewing…`                                                  | "Finally a judge picks the most idiomatic survivor."                                                                 |
| 0:25   | RedGreen tool window        | Winner panel opens bottom: `🏆 null_guard · GPT-5 mini` · `+3/-0 lines · 28s · peer 15/20 · regression 7/7` | "Winner: null_guard, GPT-5 mini. Passed seven of seven regression tests."                                            |
| 0:28   | PyCharm editor              | Gutter inlay visible at line 21: `⚡ RedGreen: fix ready · click`                       | "The fix shows up in the gutter."                                                                                    |
| 0:30   | PyCharm editor              | **Click the gutter inlay**. The `+ if refund_amount is None: return 0` patch appears green in the diff | "One click."                                                                                                         |
| 0:33   | PyCharm editor              | File shows the applied patch · tests/test_redgreen_generated.py appears in the tree     | "Applied. Test written. Done."                                                                                       |
| 0:36   | **CUT to browser**          | Leaderboard top: hero text + 4 stat numbers                                              | "Every episode logs to Supabase."                                                                                    |
| 0:40   | Browser · scroll down       | Pipeline section (5 gates) · "Why four agents" section visible                          | "And the leaderboard learns which (lens, model) pair wins on which codebase. Episode 20 reads history, biases model assignments toward the known winner. Races get faster." |
| 0:48   | Browser · scroll to bottom  | Recent races rows · null_guard winning repeatedly                                       | "Real episodes. Real wins."                                                                                          |
| 0:52   | **CUT to static end card**  | `github.com/rudranshagrawal/redgreen` + `redgreen-leaderboard.vercel.app`               | "RedGreen. Runner, peers, regression, review. Four gates. One click. Thanks."                                        |

---

## If something goes sideways mid-take

- **Subtitle stays on "Routing" past 3s** → backend is wedged. `Ctrl-C`
  the uvicorn terminal, relaunch, wait for "Application startup
  complete", try Debug again.
- **Race shows "no winner"** → `git checkout -- seeds/null_guard/` and
  `rm -f seeds/null_guard/tests/test_redgreen_generated.py`, then retry.
  The seed gets dirty after every Apply.
- **Plugin says "GREEN ✗ · patch apply failed" on multiple rows** →
  usually means you opened `seeds/` instead of `seeds/null_guard/`.
  File → Close Project, File → Open → `seeds/null_guard/`.
- **Leaderboard shows stalled "racing" rows** → already filtered in the
  UI; if they somehow show up, paste the cleanup SQL from the Supabase
  chat note into the SQL editor.

---

## The one-liner pitch (memorize)

> "Every patch has to survive the runner, pass the other agents' tests,
> not break the repo's existing tests, and then win code review — from
> another LLM trained on what makes a fix idiomatic. Four layers of
> defense. Sixty seconds, zero buttons, one click to apply."
