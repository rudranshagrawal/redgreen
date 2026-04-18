# RedGreen

**The IDE catches its own bugs and learns which model to trust.**

A JetBrains plugin that, when the debugger trips an exception, races 4 different models in parallel to produce `(failing test, patch, rationale)`. A Docker pytest runner is the referee: candidates must reproduce the bug RED, then flip it GREEN. Winner surfaces as a gutter suggestion — Tab to apply.

Every episode logs to Supabase. A per-codebase leaderboard reweights the agent pool over time. Episode 1 knows nothing. Episode 20 picks the right model first try.

## Stack

- **Models:** GPT-5 Codex (OpenAI), Llama-3.3-70B, Qwen2.5-Coder-32B, DeepSeek-V3 (Nebius Token Factory)
- **Referee:** pytest inside a Docker sandbox — no mocks
- **Backend:** FastAPI
- **Plugin:** JetBrains Platform (Kotlin), target PyCharm 2024.3+
- **Leaderboard:** Next.js on Vercel, Supabase as the datastore

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
