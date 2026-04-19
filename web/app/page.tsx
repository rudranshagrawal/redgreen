import {
  readAggregateStats,
  readLeaderboard,
  readRecentEpisodes,
  type EpisodeRow,
  type LeaderboardRow,
} from "@/lib/supabase";

// Never cache — the whole point of this page is the data is fresh.
export const dynamic = "force-dynamic";
export const revalidate = 0;

const AGENT_LABEL: Record<string, string> = {
  null_guard: "null_guard · 'what's None?'",
  input_shape: "input_shape · 'wrong shape?'",
  async_race: "async_race · 'bad ordering?'",
  config_drift: "config_drift · 'wrong config?'",
};

function humanizeMs(ms: number | null): string {
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
  // git sha: 40 hex chars
  if (/^[0-9a-f]{40}$/.test(raw)) {
    return { label: `Project @ ${raw.slice(0, 7)}`, kind: "git" };
  }
  if (raw.startsWith("seed:")) {
    return { label: `Seed: ${raw.slice(5)}`, kind: "seed" };
  }
  if (raw.startsWith("debugger:")) {
    const path = raw.slice("debugger:".length);
    const base = path.split("/").filter(Boolean).pop() ?? "project";
    return { label: base, kind: "debugger" };
  }
  if (raw.startsWith("plugin-smoke:")) {
    const path = raw.slice("plugin-smoke:".length);
    const base = path.split("/").filter(Boolean).pop() ?? "project";
    return { label: `Smoke: ${base}`, kind: "smoke" };
  }
  if (raw.startsWith("poll-test") || raw.startsWith("manual:")) {
    return { label: raw, kind: "misc" };
  }
  return { label: raw, kind: "unknown" };
}

export default async function Page() {
  const [leaderboard, episodes, stats] = await Promise.all([
    readLeaderboard(),
    readRecentEpisodes(15),
    readAggregateStats(),
  ]);

  // Group leaderboard rows by repo_hash.
  const byRepo = new Map<string, LeaderboardRow[]>();
  for (const row of leaderboard) {
    const list = byRepo.get(row.repo_hash) ?? [];
    list.push(row);
    byRepo.set(row.repo_hash, list);
  }
  // Sort repos by total attempts descending.
  const repos = Array.from(byRepo.entries()).sort((a, b) => {
    const aAttempts = a[1].reduce((s, r) => s + r.total_attempts, 0);
    const bAttempts = b[1].reduce((s, r) => s + r.total_attempts, 0);
    return bAttempts - aAttempts;
  });

  return (
    <main className="mx-auto max-w-5xl px-6 py-12 space-y-16">
      <Hero stats={stats} />

      <section>
        <h2 className="font-mono text-xs uppercase tracking-widest text-dim mb-3">
          Leaderboard · per codebase
        </h2>
        <p className="text-dim text-sm mb-6 max-w-2xl">
          Every episode is a 4-model tournament refereed by Docker-pytest.
          The leaderboard reweights over time so later episodes pick the right
          agent first try.
        </p>
        <div className="space-y-10">
          {repos.length === 0 ? (
            <div className="rounded-lg border border-line bg-panel p-6 text-dim">
              No episodes yet.
            </div>
          ) : (
            repos.map(([repo, rows]) => (
              <LeaderboardBlock key={repo} repo={repo} rows={rows} />
            ))
          )}
        </div>
      </section>

      <section>
        <h2 className="font-mono text-xs uppercase tracking-widest text-dim mb-3">
          Recent episodes
        </h2>
        <RecentTable episodes={episodes} />
      </section>

      <Footer />
    </main>
  );
}

function Hero({ stats }: { stats: { total: number; completed: number } }) {
  return (
    <header className="pt-4">
      <div className="flex items-center gap-3 mb-4">
        <div className="flex gap-1">
          <span className="w-3 h-3 rounded-full bg-red" />
          <span className="w-3 h-3 rounded-full bg-green" />
        </div>
        <span className="font-mono text-xs uppercase tracking-widest text-dim">
          RedGreen
        </span>
      </div>
      <h1 className="text-4xl md:text-5xl font-semibold tracking-tight leading-[1.1]">
        The IDE catches its own bugs and
        <br />
        learns which model to trust.
      </h1>
      <p className="mt-4 text-dim max-w-2xl">
        When the debugger trips an exception, RedGreen races four different
        models. Each returns a failing test + patch. A pytest-in-Docker referee
        decides who was right. The winner applies with Tab.
      </p>
      <div className="mt-8 flex gap-10 text-sm font-mono">
        <Stat n={stats.total} label="total episodes" />
        <Stat n={stats.completed} label="winners" />
        <Stat
          n={stats.total > 0 ? Math.round((stats.completed / stats.total) * 100) : 0}
          suffix="%"
          label="resolved"
        />
      </div>
      <div className="mt-2 text-xs text-dim/70 font-mono">
        JetBrains Codex Hackathon 2026 · github.com/rudranshagrawal/redgreen
      </div>
    </header>
  );
}

function Stat({
  n,
  label,
  suffix = "",
}: {
  n: number;
  label: string;
  suffix?: string;
}) {
  return (
    <div>
      <div className="text-3xl font-semibold">
        {n}
        <span className="text-dim text-lg">{suffix}</span>
      </div>
      <div className="text-xs uppercase tracking-widest text-dim mt-1">
        {label}
      </div>
    </div>
  );
}

function LeaderboardBlock({
  repo,
  rows,
}: {
  repo: string;
  rows: LeaderboardRow[];
}) {
  rows.sort((a, b) => b.wins - a.wins || a.avg_ms - b.avg_ms);
  const top = rows[0];
  const confidence =
    top && top.total_attempts > 0
      ? Math.round((top.wins / top.total_attempts) * 100)
      : 0;

  const { label: repoLabel, kind: repoKind } = humanizeRepo(repo);
  return (
    <div className="rounded-xl border border-line bg-panel overflow-hidden">
      <div className="flex items-baseline justify-between px-5 py-3 border-b border-line">
        <div className="flex items-baseline gap-2 truncate">
          <span className="font-medium truncate">{repoLabel}</span>
          <span className="font-mono text-[10px] uppercase tracking-widest text-dim">{repoKind}</span>
        </div>
        {top && (
          <div className="text-xs text-dim">
            predicts{" "}
            <span className="text-green font-medium">
              {AGENT_LABEL[top.agent] ?? top.agent}
            </span>{" "}
            ({confidence}% confidence)
          </div>
        )}
      </div>
      <table className="w-full text-sm">
        <thead className="text-left text-dim text-xs uppercase tracking-widest">
          <tr>
            <th className="px-5 py-2 font-normal">Agent</th>
            <th className="px-5 py-2 font-normal text-right">Wins</th>
            <th className="px-5 py-2 font-normal text-right">Losses</th>
            <th className="px-5 py-2 font-normal text-right">Avg time</th>
            <th className="px-5 py-2 font-normal text-right">Attempts</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={r.agent}
              className={i === 0 && r.wins > 0 ? "bg-green/5" : "border-t border-line/50"}
            >
              <td className="px-5 py-2.5 font-mono text-xs">
                {i === 0 && r.wins > 0 ? "🏆 " : "   "}
                {AGENT_LABEL[r.agent] ?? r.agent}
              </td>
              <td className="px-5 py-2.5 text-right font-mono">
                <span className={r.wins > 0 ? "text-green" : "text-dim"}>
                  {r.wins}
                </span>
              </td>
              <td className="px-5 py-2.5 text-right font-mono">
                <span className={r.losses > 0 ? "text-red" : "text-dim"}>
                  {r.losses}
                </span>
              </td>
              <td className="px-5 py-2.5 text-right font-mono text-dim">
                {humanizeMs(r.avg_ms)}
              </td>
              <td className="px-5 py-2.5 text-right font-mono text-dim">
                {r.total_attempts}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RecentTable({ episodes }: { episodes: EpisodeRow[] }) {
  if (episodes.length === 0) {
    return (
      <div className="rounded-lg border border-line bg-panel p-6 text-dim">
        No episodes yet.
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-line bg-panel overflow-hidden">
      <table className="w-full text-sm">
        <thead className="text-left text-dim text-xs uppercase tracking-widest">
          <tr>
            <th className="px-5 py-2 font-normal">When</th>
            <th className="px-5 py-2 font-normal">File</th>
            <th className="px-5 py-2 font-normal">Winner</th>
            <th className="px-5 py-2 font-normal">Model</th>
            <th className="px-5 py-2 font-normal text-right">Time</th>
          </tr>
        </thead>
        <tbody>
          {episodes.map((e, i) => (
            <tr
              key={e.id}
              className={i > 0 ? "border-t border-line/50" : ""}
            >
              <td className="px-5 py-2.5 font-mono text-xs text-dim whitespace-nowrap">
                {humanizeAgo(e.created_at)}
              </td>
              <td className="px-5 py-2.5 font-mono text-xs truncate max-w-[20rem]">
                {e.frame_file}:{e.frame_line}
              </td>
              <td className="px-5 py-2.5 font-mono text-xs">
                {e.winner_agent ? (
                  <span className="text-green">{AGENT_LABEL[e.winner_agent] ?? e.winner_agent}</span>
                ) : e.state === "racing" ? (
                  <span className="text-amber">racing…</span>
                ) : (
                  <span className="text-red">no winner</span>
                )}
              </td>
              <td className="px-5 py-2.5 font-mono text-xs text-dim">
                {e.winner_model ?? "—"}
              </td>
              <td className="px-5 py-2.5 text-right font-mono text-xs text-dim">
                {humanizeMs(e.total_elapsed_ms)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Footer() {
  return (
    <footer className="pt-8 border-t border-line text-xs text-dim">
      <div className="flex items-center justify-between">
        <div>
          Built at the JetBrains Codex Hackathon ·{" "}
          <a
            className="underline decoration-dim/40 underline-offset-4 hover:text-fg"
            href="https://github.com/rudranshagrawal/redgreen"
          >
            source
          </a>
        </div>
        <div className="font-mono">auto-refreshes on reload</div>
      </div>
    </footer>
  );
}
