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

// ---------- helpers ----------

function humanizeMs(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m${Math.floor((ms % 60_000) / 1000)}s`;
}

function humanizeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

function humanizeRepo(raw: string): string {
  if (/^[0-9a-f]{40}$/.test(raw)) return `project @ ${raw.slice(0, 7)}`;
  if (raw.startsWith("seed:")) return `seed · ${raw.slice(5)}`;
  if (raw.startsWith("debugger:")) return raw.slice("debugger:".length).split("/").filter(Boolean).pop() ?? "project";
  if (raw.startsWith("plugin-smoke:")) return `smoke · ${raw.slice("plugin-smoke:".length).split("/").filter(Boolean).pop() ?? ""}`;
  if (raw.startsWith("manual:")) return raw.slice("manual:".length).split("/").filter(Boolean).pop() ?? "manual";
  if (raw.startsWith("poll-test")) return "backend probe";
  return raw;
}

function shortenModel(model: string): string {
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
    readRecentEpisodes(12),
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
    <main className="mx-auto max-w-4xl px-6 py-14 space-y-16">
      <Hero stats={stats} insights={insights} />
      <Pipeline />
      <WhyFourAgents />
      <LeaderboardsSection repos={repos} />
      <RecentSection episodes={episodes} />
      <Footer />
    </main>
  );
}

// ---------- top: hero ----------

function Hero({ stats, insights }: { stats: { total: number; completed: number }; insights: Insight }) {
  const resolvedPct = stats.total > 0 ? Math.round((stats.completed / stats.total) * 100) : 0;
  return (
    <header className="pt-4">
      <div className="flex items-center gap-3 mb-6">
        <span className="w-2 h-2 rounded-full bg-red" />
        <span className="w-2 h-2 rounded-full bg-green" />
        <span className="font-mono text-[10px] uppercase tracking-widest text-dim">
          RedGreen · live
        </span>
      </div>
      <h1 className="text-4xl md:text-5xl font-semibold tracking-tight leading-[1.1]">
        The IDE catches its own bugs.
      </h1>
      <p className="mt-5 text-dim max-w-xl leading-relaxed">
        A JetBrains plugin. When the PyCharm debugger trips an exception,
        four models race to produce a patch. Three automated gates filter
        out hacks. A fourth picks the most idiomatic survivor. The winning
        patch shows up as a gutter suggestion, click-to-apply.
      </p>
      <div className="mt-10 flex flex-wrap gap-x-10 gap-y-4 text-sm">
        <HeroStat n={stats.total} label="episodes run" />
        <HeroStat n={stats.completed} label="winners" />
        <HeroStat n={resolvedPct} suffix="%" label="resolve rate" />
        <HeroStat label="median fix time" value={humanizeMs(insights.avgMs)} />
      </div>
    </header>
  );
}

function HeroStat({ n, value, suffix = "", label }: { n?: number; value?: string; suffix?: string; label: string }) {
  return (
    <div>
      <div className="text-2xl font-semibold">
        {value ?? n}
        {suffix && <span className="text-dim text-base">{suffix}</span>}
      </div>
      <div className="text-[11px] uppercase tracking-widest text-dim mt-0.5">{label}</div>
    </div>
  );
}

// ---------- top: pipeline ----------

function Pipeline() {
  const gates = [
    {
      n: "01",
      title: "Race",
      body: "Four models generate a failing test + patch + rationale in parallel. Each model sees the same stacktrace but is steered by a different hypothesis lens — 'is this a null bug?', 'wrong input shape?', 'race condition?', etc.",
    },
    {
      n: "02",
      title: "Runner",
      body: "Each candidate runs inside a fresh Docker pytest sandbox. Test must reproduce the bug without the patch (RED), then pass with the patch (GREEN). Syntax errors and broken diffs die here.",
    },
    {
      n: "03",
      title: "Peers",
      body: "Every survivor's patch is re-run against every other survivor's test. A patch that only passes its own test is a hack; one that passes the majority is robust. Cheaters get filtered.",
    },
    {
      n: "04",
      title: "Regression",
      body: "The patch runs against the repo's existing test suite. If it fixed the target bug but broke an unrelated feature, it's out. The CI gate — in two seconds.",
    },
    {
      n: "05",
      title: "Judge",
      body: "A small LLM looks at the remaining survivors and picks the most idiomatic: explicit guard over generic catch, domain exception over bare Exception, matches project conventions. Ties broken by peer-test count.",
    },
  ];
  return (
    <section>
      <SectionHead eyebrow="What happens" title="One exception · five gates · one winner" />
      <div className="mt-6 divide-y divide-line/60 rounded-xl border border-line overflow-hidden">
        {gates.map((g) => (
          <div key={g.n} className="grid md:grid-cols-[80px_1fr] gap-4 px-5 py-4">
            <div className="font-mono text-[11px] uppercase tracking-widest text-dim pt-1">{g.n} · {g.title}</div>
            <p className="text-sm text-dim leading-relaxed">{g.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

// ---------- top: why four agents ----------

function WhyFourAgents() {
  return (
    <section>
      <SectionHead eyebrow="The number four" title="Why four models and not one, or sixteen?" />
      <div className="mt-6 grid md:grid-cols-3 gap-4">
        <ReasonCard
          title="One isn't enough"
          body="Different LLMs make different mistakes on the same crash. A single model locks you into one hypothesis — null guard vs. input validation vs. race condition. You want the race."
        />
        <ReasonCard
          title="Sixteen isn't better"
          body="Each extra model adds ~$0.02 and 5-15s. A 12-entry router scores the stacktrace and picks the top four lenses. Past that, marginal wins fall off a cliff — we measured."
        />
        <ReasonCard
          title="Diversity > depth"
          body="The four slots rotate across 12 hypothesis lenses. A TypeError routes to null_guard + input_shape + api_contract. An ImportError routes to dependency_missing first. Same model pool, different lenses."
        />
      </div>
      <p className="mt-5 text-xs text-dim font-mono max-w-2xl">
        side effect: the leaderboard learns which (lens, model) pair wins on
        which codebase. Episode 20 reads history and biases lens→model
        assignments toward known winners — so later races resolve faster.
      </p>
    </section>
  );
}

function ReasonCard({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-line bg-panel p-5">
      <div className="font-medium mb-2">{title}</div>
      <p className="text-sm text-dim leading-relaxed">{body}</p>
    </div>
  );
}

// ---------- bottom: leaderboards ----------

function LeaderboardsSection({ repos }: { repos: [string, LeaderboardRow[]][] }) {
  return (
    <section>
      <SectionHead eyebrow="Per codebase" title="Who wins where" copy="One block per project. The bar shows what fraction of attempts each lens won. This is what the router reads to bias future episodes." />
      <div className="mt-6 space-y-4">
        {repos.length === 0 ? (
          <EmptyCard text="No episodes logged yet." />
        ) : (
          repos.slice(0, 6).map(([repo, rows]) => <LeaderboardBlock key={repo} repo={repo} rows={rows} />)
        )}
      </div>
    </section>
  );
}

function LeaderboardBlock({ repo, rows }: { repo: string; rows: LeaderboardRow[] }) {
  rows.sort((a, b) => b.wins - a.wins || a.avg_ms - b.avg_ms);
  const top = rows[0];
  const confidence = top && top.total_attempts > 0 ? Math.round((top.wins / top.total_attempts) * 100) : 0;
  const totalAttempts = rows.reduce((s, r) => s + r.total_attempts, 0);

  return (
    <div className="rounded-xl border border-line bg-panel overflow-hidden">
      <div className="flex items-baseline justify-between px-5 py-3 border-b border-line">
        <div className="truncate">
          <span className="text-sm font-medium">{humanizeRepo(repo)}</span>
          <span className="ml-3 font-mono text-[10px] text-dim">{totalAttempts} attempts</span>
        </div>
        {top && top.wins > 0 && (
          <div className="text-[11px] font-mono text-dim shrink-0">
            predicts <span className="text-fg">{top.agent}</span> · {confidence}%
          </div>
        )}
      </div>
      <div className="divide-y divide-line/40">
        {rows.slice(0, 5).map((r, i) => {
          const isChamp = i === 0 && r.wins > 0;
          const total = r.wins + r.losses;
          const winPct = total > 0 ? (r.wins / total) * 100 : 0;
          return (
            <div key={r.agent} className="px-5 py-3 flex items-center gap-4">
              <div className="w-44 shrink-0 font-mono text-xs truncate">
                {isChamp ? <span className="text-green">▸ </span> : <span className="text-dim">· </span>}
                {r.agent}
              </div>
              <div className="flex-1 h-1 rounded-full bg-line overflow-hidden">
                <div className="h-full bg-green" style={{ width: `${winPct}%` }} />
              </div>
              <div className="w-20 shrink-0 text-right font-mono text-[11px] text-dim">
                {r.wins}W · {r.losses}L
              </div>
              <div className="w-14 shrink-0 text-right font-mono text-[11px] text-dim">
                {humanizeMs(r.avg_ms)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------- bottom: recent ----------

function RecentSection({ episodes }: { episodes: EpisodeRow[] }) {
  return (
    <section>
      <SectionHead eyebrow="Recent" title="The last races" copy="Each row is one exception the plugin caught. Green means a patch is ready to apply; amber means still racing; red means no patch survived the gates." />
      <div className="mt-6 rounded-xl border border-line overflow-hidden divide-y divide-line/60">
        {episodes.length === 0 ? (
          <div className="p-5 text-sm text-dim">No episodes yet.</div>
        ) : (
          episodes.map((e) => <EpisodeRow key={e.id} episode={e} />)
        )}
      </div>
    </section>
  );
}

function EpisodeRow({ episode: e }: { episode: EpisodeRow }) {
  const isWin = e.state === "completed" && !!e.winner_agent;
  const isRacing = e.state === "racing";
  const statusColor = isWin ? "text-green" : isRacing ? "text-amber" : "text-red";
  const statusLabel = isWin ? "won" : isRacing ? "racing" : "no winner";

  return (
    <div className="grid md:grid-cols-[auto_1fr_auto_auto] items-baseline gap-4 px-5 py-3">
      <div className={`font-mono text-[11px] uppercase tracking-widest w-16 shrink-0 ${statusColor}`}>
        {statusLabel}
      </div>
      <div className="min-w-0">
        <div className="font-mono text-xs truncate">{e.frame_file}:{e.frame_line}</div>
        <div className="text-[11px] text-dim truncate">
          {isWin
            ? <>{e.winner_agent} · {e.winner_model ? shortenModel(e.winner_model) : "?"}</>
            : isRacing
            ? "four agents still running…"
            : "all candidates eliminated"}
        </div>
      </div>
      <div className="font-mono text-[11px] text-dim shrink-0 w-16 text-right">
        {humanizeMs(e.total_elapsed_ms)}
      </div>
      <div className="font-mono text-[11px] text-dim shrink-0 w-16 text-right">
        {humanizeAgo(e.created_at)}
      </div>
    </div>
  );
}

// ---------- shared ----------

function SectionHead({ eyebrow, title, copy }: { eyebrow: string; title: string; copy?: string }) {
  return (
    <div>
      <div className="font-mono text-[10px] uppercase tracking-widest text-dim mb-1.5">{eyebrow}</div>
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      {copy && <p className="text-sm text-dim mt-2 max-w-2xl leading-relaxed">{copy}</p>}
    </div>
  );
}

function EmptyCard({ text }: { text: string }) {
  return <div className="rounded-xl border border-line bg-panel p-6 text-dim text-sm">{text}</div>;
}

function Footer() {
  return (
    <footer className="pt-12 border-t border-line">
      <div className="flex flex-wrap items-center justify-between gap-3 text-[11px] font-mono text-dim">
        <div>
          JetBrains Codex Hackathon 2026 · built 2026-04-18 / 2026-04-19 ·{" "}
          <a className="underline decoration-dim/40 underline-offset-4 hover:text-fg" href="https://github.com/rudranshagrawal/redgreen">
            github.com/rudranshagrawal/redgreen
          </a>
        </div>
        <div>data refreshes on reload</div>
      </div>
    </footer>
  );
}
