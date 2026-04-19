import {
  readAggregateStats,
  readInsights,
  readLeaderboard,
  readRecentEpisodes,
  type EpisodeRow,
  type Insight,
  type LeaderboardRow,
} from "@/lib/supabase";

export const dynamic = "force-dynamic";
export const revalidate = 0;

// ---------- hypothesis vocabulary (kept in sync with plugin) ----------

const AGENT_LABEL: Record<string, { short: string; nickname: string; color: string }> = {
  null_guard:          { short: "null_guard",     nickname: "what's None?",         color: "#E04B4B" },
  input_shape:         { short: "input_shape",    nickname: "wrong shape?",         color: "#CF8A4B" },
  async_race:          { short: "async_race",     nickname: "bad ordering?",        color: "#9D6AE0" },
  config_drift:        { short: "config_drift",   nickname: "wrong config?",        color: "#4BA4E0" },
  math_error:          { short: "math_error",     nickname: "bad arithmetic?",      color: "#E04BAD" },
  resource_leak:       { short: "resource_leak",  nickname: "forgot to close?",     color: "#4BE0B6" },
  encoding:            { short: "encoding",       nickname: "bytes vs str?",        color: "#E0C74B" },
  recursion:           { short: "recursion",      nickname: "no base case?",        color: "#70E04B" },
  api_contract:        { short: "api_contract",   nickname: "signature drift?",     color: "#4BE0E0" },
  timezone:            { short: "timezone",       nickname: "tz-aware mix?",        color: "#B0B0B0" },
  auth_permission:     { short: "auth_permission",nickname: "401/403?",             color: "#E09A4B" },
  dependency_missing:  { short: "dependency",     nickname: "bad import?",          color: "#4B9AE0" },
};

function agentStyle(agent: string): { short: string; nickname: string; color: string } {
  return AGENT_LABEL[agent] ?? { short: agent, nickname: "", color: "#888" };
}

// ---------- formatting helpers ----------

function humanizeMs(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m${Math.floor((ms % 60_000) / 1000)}s`;
}

function humanizeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = Date.now() - then;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function humanizeRepo(raw: string): { label: string; kind: string } {
  if (/^[0-9a-f]{40}$/.test(raw)) return { label: `Project @ ${raw.slice(0, 7)}`, kind: "git" };
  if (raw.startsWith("seed:"))         return { label: `Seed: ${raw.slice(5)}`, kind: "seed" };
  if (raw.startsWith("debugger:"))     return { label: raw.slice("debugger:".length).split("/").filter(Boolean).pop() ?? "project", kind: "debugger" };
  if (raw.startsWith("plugin-smoke:")) return { label: `Smoke: ${raw.slice("plugin-smoke:".length).split("/").filter(Boolean).pop() ?? "project"}`, kind: "smoke" };
  if (raw.startsWith("manual:"))       return { label: raw.slice("manual:".length).split("/").filter(Boolean).pop() ?? "manual", kind: "manual" };
  if (raw.startsWith("poll-test"))     return { label: "Backend smoke test", kind: "probe" };
  return { label: raw, kind: "other" };
}

function shortenModel(model: string): string {
  // "meta-llama/Llama-3.3-70B-Instruct" -> "Llama 3.3 70B"
  // "deepseek-ai/DeepSeek-V3.2-fast" -> "DeepSeek V3.2"
  // "Qwen/Qwen3-32B" -> "Qwen3 32B"
  if (model.includes("Llama-3.3-70B")) return "Llama 3.3 70B";
  if (model.includes("DeepSeek-V3.2")) return "DeepSeek V3.2";
  if (model.includes("Qwen3-32B")) return "Qwen3 32B";
  if (model === "gpt-5-mini") return "GPT-5 mini";
  if (model === "gpt-5") return "GPT-5";
  return model;
}

// ---------- page ----------

export default async function Page() {
  const [leaderboard, episodes, stats, insights] = await Promise.all([
    readLeaderboard(),
    readRecentEpisodes(18),
    readAggregateStats(),
    readInsights(),
  ]);

  const byRepo = new Map<string, LeaderboardRow[]>();
  for (const row of leaderboard) {
    const list = byRepo.get(row.repo_hash) ?? [];
    list.push(row);
    byRepo.set(row.repo_hash, list);
  }
  const repos = Array.from(byRepo.entries()).sort((a, b) => {
    const aAttempts = a[1].reduce((s, r) => s + r.total_attempts, 0);
    const bAttempts = b[1].reduce((s, r) => s + r.total_attempts, 0);
    return bAttempts - aAttempts;
  });

  return (
    <main className="mx-auto max-w-5xl px-6 py-12 space-y-14">
      <Hero stats={stats} />
      <HowItWorks />
      <InsightsStrip insights={insights} />

      <section>
        <SectionHead
          eyebrow="Per codebase"
          title="Which lens wins on which project?"
          copy="Each block is a project. The bar shows which hypothesis wins there most often. Over time these drift — that's the learning."
        />
        <div className="space-y-6">
          {repos.length === 0 ? (
            <EmptyCard text="No episodes yet." />
          ) : (
            repos.map(([repo, rows]) => <LeaderboardBlock key={repo} repo={repo} rows={rows} />)
          )}
        </div>
      </section>

      <section>
        <SectionHead
          eyebrow="Recent"
          title="The last 18 races"
          copy="Each card is one exception the plugin caught. Green bar = won (patch available to apply). Amber = no winner this round."
        />
        <RecentGrid episodes={episodes} />
      </section>

      <Footer />
    </main>
  );
}

// ---------- sections ----------

function Hero({ stats }: { stats: { total: number; completed: number } }) {
  const resolvedPct = stats.total > 0 ? Math.round((stats.completed / stats.total) * 100) : 0;
  return (
    <header className="pt-4">
      <div className="flex items-center gap-3 mb-5">
        <div className="flex gap-1">
          <span className="w-3 h-3 rounded-full bg-red" />
          <span className="w-3 h-3 rounded-full bg-green" />
        </div>
        <span className="font-mono text-xs uppercase tracking-widest text-dim">
          RedGreen · live leaderboard
        </span>
      </div>
      <h1 className="text-4xl md:text-5xl font-semibold tracking-tight leading-[1.1]">
        The IDE catches its own bugs and
        <br />
        learns which model to trust.
      </h1>
      <p className="mt-5 text-dim max-w-2xl leading-relaxed">
        When the PyCharm debugger trips an exception, the RedGreen plugin races
        up to four models in parallel — each with a different "what kind of bug
        is this?" lens. A Docker-pytest referee cross-validates the patches
        against each other. The one that passes the most peer tests wins.
      </p>
      <div className="mt-8 grid grid-cols-3 gap-6 max-w-md">
        <Stat n={stats.total} label="episodes" />
        <Stat n={stats.completed} label="winners" />
        <Stat n={resolvedPct} suffix="%" label="resolved" />
      </div>
      <div className="mt-3 text-xs text-dim/70 font-mono">
        JetBrains Codex Hackathon 2026 · <a className="underline decoration-dim/40 underline-offset-4 hover:text-fg" href="https://github.com/rudranshagrawal/redgreen">github.com/rudranshagrawal/redgreen</a>
      </div>
    </header>
  );
}

function HowItWorks() {
  const steps = [
    {
      n: "1",
      title: "Capture",
      body: "Debugger pauses on an exception. Plugin reads the frame, stacktrace, and surrounding source. If the user's code isn't in the stack (framework-caught SyntaxError), the plugin falls back to PyCharm's parser.",
    },
    {
      n: "2",
      title: "Race",
      body: "A router picks 4 of 12 hypothesis lenses based on the exception type. Each lens is paired with a different model (GPT-5 mini, Llama 3.3, Qwen 3, DeepSeek V3.2). All four produce a (failing test, patch, rationale) in parallel.",
    },
    {
      n: "3",
      title: "Cross-validate",
      body: "Every patch is tested against every agent's tests plus any pre-existing repo tests. Most peer tests passed wins — hacks that only satisfy their own test get filtered out.",
    },
  ];
  return (
    <section>
      <SectionHead
        eyebrow="How it works"
        title="Four models race. Docker refs."
        copy="No trusting an LLM to self-grade. Every proposed fix has to survive the others' tests."
      />
      <div className="grid md:grid-cols-3 gap-4">
        {steps.map((s) => (
          <div key={s.n} className="rounded-xl border border-line bg-panel p-5">
            <div className="flex items-baseline gap-3 mb-2">
              <span className="font-mono text-xs text-dim">STEP {s.n}</span>
              <span className="font-medium">{s.title}</span>
            </div>
            <p className="text-sm text-dim leading-relaxed">{s.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

function InsightsStrip({ insights }: { insights: Insight }) {
  const { fastestMs, fastestFile, avgMs, topModel, topAgent, uniqueFiles } = insights;
  return (
    <section>
      <div className="grid md:grid-cols-4 gap-3 text-sm">
        <InsightCell label="Fastest fix" value={humanizeMs(fastestMs)} sub={fastestFile ?? ""} />
        <InsightCell label="Avg race time" value={humanizeMs(avgMs)} sub="across completed races" />
        <InsightCell
          label="Best model"
          value={topModel ? shortenModel(topModel.model) : "—"}
          sub={topModel ? `${topModel.wins} wins` : ""}
        />
        <InsightCell
          label="Top lens"
          value={topAgent ? agentStyle(topAgent.agent).short : "—"}
          sub={topAgent ? `${topAgent.wins} wins · ${uniqueFiles} unique files` : ""}
          accent={topAgent ? agentStyle(topAgent.agent).color : undefined}
        />
      </div>
    </section>
  );
}

function InsightCell({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub: string;
  accent?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-panel p-4">
      <div className="text-[10px] uppercase tracking-widest text-dim">{label}</div>
      <div className="mt-1 text-xl font-medium" style={accent ? { color: accent } : undefined}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-dim truncate">{sub}</div>}
    </div>
  );
}

function SectionHead({ eyebrow, title, copy }: { eyebrow: string; title: string; copy: string }) {
  return (
    <div className="mb-5">
      <div className="font-mono text-xs uppercase tracking-widest text-dim mb-1">{eyebrow}</div>
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      <p className="text-sm text-dim mt-1 max-w-2xl">{copy}</p>
    </div>
  );
}

function Stat({ n, label, suffix = "" }: { n: number; label: string; suffix?: string }) {
  return (
    <div>
      <div className="text-3xl font-semibold">
        {n}
        <span className="text-dim text-lg">{suffix}</span>
      </div>
      <div className="text-[11px] uppercase tracking-widest text-dim mt-1">{label}</div>
    </div>
  );
}

function LeaderboardBlock({ repo, rows }: { repo: string; rows: LeaderboardRow[] }) {
  rows.sort((a, b) => b.wins - a.wins || a.avg_ms - b.avg_ms);
  const top = rows[0];
  const confidence = top && top.total_attempts > 0 ? Math.round((top.wins / top.total_attempts) * 100) : 0;
  const { label: repoLabel, kind: repoKind } = humanizeRepo(repo);
  const totalAttempts = rows.reduce((s, r) => s + r.total_attempts, 0);

  return (
    <div className="rounded-xl border border-line bg-panel overflow-hidden">
      <div className="flex items-baseline justify-between px-5 py-3 border-b border-line">
        <div className="flex items-baseline gap-2 truncate">
          <span className="font-medium truncate">{repoLabel}</span>
          <span className="font-mono text-[10px] uppercase tracking-widest text-dim">{repoKind}</span>
          <span className="font-mono text-[10px] text-dim">· {totalAttempts} attempts</span>
        </div>
        {top && top.wins > 0 && (
          <div className="text-xs text-dim">
            predicts{" "}
            <span style={{ color: agentStyle(top.agent).color }} className="font-medium">
              {agentStyle(top.agent).short}
            </span>{" "}
            ({confidence}%)
          </div>
        )}
      </div>
      <div className="divide-y divide-line/40">
        {rows.map((r, i) => {
          const st = agentStyle(r.agent);
          const winRate = r.total_attempts > 0 ? r.wins / r.total_attempts : 0;
          const isChamp = i === 0 && r.wins > 0;
          return (
            <div key={r.agent} className={`px-5 py-3 flex items-center gap-4 ${isChamp ? "bg-green/5" : ""}`}>
              <div className="w-52 shrink-0 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full shrink-0" style={{ background: st.color }} />
                <div className="min-w-0">
                  <div className="text-sm font-mono truncate">
                    {isChamp && "🏆 "}
                    {st.short}
                  </div>
                  <div className="text-[11px] text-dim truncate">"{st.nickname}"</div>
                </div>
              </div>
              <div className="flex-1">
                <WinLossBar wins={r.wins} losses={r.losses} color={st.color} />
              </div>
              <div className="text-xs font-mono text-dim w-24 text-right shrink-0">
                <span className={r.wins > 0 ? "text-green" : ""}>{r.wins}W</span>
                {" · "}
                <span className={r.losses > 0 ? "text-red" : ""}>{r.losses}L</span>
              </div>
              <div className="text-xs font-mono text-dim w-20 text-right shrink-0">
                {humanizeMs(r.avg_ms)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function WinLossBar({ wins, losses, color }: { wins: number; losses: number; color: string }) {
  const total = wins + losses;
  if (total === 0) {
    return <div className="h-1.5 rounded-full bg-line/60 w-full" />;
  }
  const winPct = (wins / total) * 100;
  return (
    <div className="h-1.5 rounded-full bg-line/60 w-full overflow-hidden flex">
      <div className="h-full" style={{ width: `${winPct}%`, background: color }} />
      <div className="h-full bg-red/60" style={{ width: `${100 - winPct}%` }} />
    </div>
  );
}

function RecentGrid({ episodes }: { episodes: EpisodeRow[] }) {
  if (episodes.length === 0) {
    return <EmptyCard text="No episodes yet." />;
  }
  return (
    <div className="grid md:grid-cols-2 gap-3">
      {episodes.map((e) => (
        <EpisodeCard key={e.id} episode={e} />
      ))}
    </div>
  );
}

function EpisodeCard({ episode: e }: { episode: EpisodeRow }) {
  const isWin = e.state === "completed" && !!e.winner_agent;
  const isRacing = e.state === "racing";
  const st = e.winner_agent ? agentStyle(e.winner_agent) : null;

  return (
    <div className="relative rounded-xl border border-line bg-panel overflow-hidden">
      <div
        className="absolute top-0 bottom-0 left-0 w-0.5"
        style={{
          background: isWin ? (st?.color ?? "#3FB950") : isRacing ? "#CF8A4B" : "#E04B4B",
        }}
      />
      <div className="px-5 py-3.5 pl-6">
        <div className="flex items-baseline justify-between gap-3">
          <div className="font-mono text-xs truncate">{e.frame_file}:{e.frame_line}</div>
          <div className="text-[11px] text-dim shrink-0 font-mono">{humanizeAgo(e.created_at)}</div>
        </div>
        <div className="mt-1.5 flex items-center gap-2">
          {isWin && st ? (
            <>
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: st.color }} />
              <span className="text-sm" style={{ color: st.color }}>{st.short}</span>
              <span className="text-xs text-dim truncate">"{st.nickname}"</span>
            </>
          ) : isRacing ? (
            <span className="text-sm text-amber">racing…</span>
          ) : (
            <span className="text-sm text-red">no winner</span>
          )}
        </div>
        <div className="mt-2 flex items-center justify-between text-[11px] text-dim font-mono">
          <span className="truncate">{e.winner_model ? shortenModel(e.winner_model) : "—"}</span>
          <span>{humanizeMs(e.total_elapsed_ms)}</span>
        </div>
      </div>
    </div>
  );
}

function EmptyCard({ text }: { text: string }) {
  return <div className="rounded-lg border border-line bg-panel p-6 text-dim">{text}</div>;
}

function Footer() {
  return (
    <footer className="pt-10 border-t border-line text-xs text-dim">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          Built at the JetBrains Codex Hackathon ·{" "}
          <a className="underline decoration-dim/40 underline-offset-4 hover:text-fg" href="https://github.com/rudranshagrawal/redgreen">
            source
          </a>
        </div>
        <div className="font-mono">data refreshes on reload · no caching</div>
      </div>
    </footer>
  );
}
