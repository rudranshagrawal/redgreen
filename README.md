# RedGreen

**The IDE catches its own bugs and learns which model to trust.**

A JetBrains plugin that, when the debugger trips an exception, races up to 4 models in parallel from a catalog of 12 hypothesis lenses. Every patch has to survive three layers of defense:

1. **Runner** — pytest inside Docker. Did the patch compile and pass a test?
2. **Peers** — cross-validation. Did the patch also pass the *other* agents' tests?
3. **Review** — a quality judge (small LLM call) picks the most idiomatic survivor — rejects "hacks" like literal-value swaps that silence the crash without addressing the cause.

Every episode logs to Supabase. A per-codebase leaderboard reweights the agent pool over time — episode 1 shuffles randomly, episode 20 reads history and picks the model that won on *this codebase* first.

## Stack

- **Models:** GPT-5 mini (OpenAI), Llama-3.3-70B, Qwen3-32B, DeepSeek-V3.2-fast (Nebius Token Factory) — shuffled across lenses per episode
- **Router:** exception-type + frame-keyword scorer (`backend/router.py`) picks the top 4 of 12 hypothesis lenses per episode
- **Referee:** pytest inside a Docker sandbox — no mocks
- **Judge:** GPT-5 mini again, called once per episode on survivor patches, 12s hard timeout
- **Backend:** FastAPI with async background tasks
- **Plugin:** JetBrains Platform (Kotlin), target PyCharm 2024.3+; PSI fallback for SyntaxErrors; inline editor inlay + click-to-apply
- **Leaderboard:** Next.js 15 on Vercel, Supabase as the datastore
- **Indexer:** plugin-side background scan of the user's Python project, feeds codebase conventions into every agent's prompt

## Demo

Demo video: *TBD (posted Sunday).* See [`CLAUDE.md`](./CLAUDE.md) for the 60-second demo script, which is the spec.

## Quickstart

```bash
cp .env.example .env.local   # fill in keys
just check                   # env + api reachability
just seed null_guard         # run one episode end-to-end from CLI
just dev                     # backend + runner + web
just plugin-run              # open PyCharm sandbox with plugin loaded
```

## Status

Built at the **JetBrains Codex Hackathon** ("The IDE Reimagined"), 2026-04-18 / 2026-04-19, San Francisco. All code in this repo dates from 2026-04-18 onward (hackathon rule 7B: no prior work).

## License

MIT. See [`LICENSE`](./LICENSE).

## Sponsors in play

Codex · Nebius · Supabase · Docker · Vercel · (stretch: AuthZed, BKey)
