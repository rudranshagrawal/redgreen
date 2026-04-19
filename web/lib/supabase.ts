import { createClient, SupabaseClient } from "@supabase/supabase-js";

let _client: SupabaseClient | null = null;

export function supabase(): SupabaseClient {
  if (_client) return _client;
  const url = process.env.SUPABASE_URL;
  const key =
    process.env.SUPABASE_SERVICE_ROLE_KEY ?? process.env.SUPABASE_ANON_KEY;
  if (!url || !key) {
    throw new Error(
      "Set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) as env vars.",
    );
  }
  _client = createClient(url, key, {
    auth: { persistSession: false },
  });
  return _client;
}

// ---------- types mirroring supabase/schema.sql ----------

export type LeaderboardRow = {
  repo_hash: string;
  agent: "null_guard" | "input_shape" | "async_race" | "config_drift";
  wins: number;
  losses: number;
  avg_ms: number;
  total_attempts: number;
};

export type EpisodeRow = {
  id: string;
  created_at: string;
  repo_hash: string;
  frame_file: string;
  frame_line: number;
  state: "racing" | "completed" | "no_winner";
  winner_agent: string | null;
  winner_model: string | null;
  total_elapsed_ms: number | null;
  notes: string | null;
};

// ---------- queries ----------

export async function readLeaderboard(): Promise<LeaderboardRow[]> {
  const { data, error } = await supabase()
    .from("leaderboard")
    .select("*")
    .order("wins", { ascending: false });
  if (error) throw error;
  return (data ?? []) as LeaderboardRow[];
}

export async function readRecentEpisodes(limit = 15): Promise<EpisodeRow[]> {
  const { data, error } = await supabase()
    .from("episodes")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(limit);
  if (error) throw error;
  return (data ?? []) as EpisodeRow[];
}

export async function readAggregateStats() {
  const [{ count: total }, { count: completed }] = await Promise.all([
    supabase().from("episodes").select("*", { count: "exact", head: true }),
    supabase()
      .from("episodes")
      .select("*", { count: "exact", head: true })
      .eq("state", "completed"),
  ]);
  return { total: total ?? 0, completed: completed ?? 0 };
}

export type Insight = {
  fastestMs: number | null;
  fastestFile: string | null;
  avgMs: number | null;
  topModel: { model: string; wins: number } | null;
  topAgent: { agent: string; wins: number } | null;
  uniqueFiles: number;
};

export async function readInsights(): Promise<Insight> {
  const { data: episodes, error } = await supabase()
    .from("episodes")
    .select("state,winner_agent,winner_model,total_elapsed_ms,frame_file")
    .eq("state", "completed")
    .order("created_at", { ascending: false })
    .limit(200);
  if (error) throw error;
  const rows = episodes ?? [];
  if (rows.length === 0) {
    return { fastestMs: null, fastestFile: null, avgMs: null, topModel: null, topAgent: null, uniqueFiles: 0 };
  }

  let fastest = rows[0];
  for (const r of rows) {
    if ((r.total_elapsed_ms ?? Infinity) < (fastest.total_elapsed_ms ?? Infinity)) fastest = r;
  }

  const totalMs = rows.reduce((s, r) => s + (r.total_elapsed_ms ?? 0), 0);
  const avgMs = Math.round(totalMs / rows.length);

  const modelCounts = new Map<string, number>();
  const agentCounts = new Map<string, number>();
  const files = new Set<string>();
  for (const r of rows) {
    if (r.winner_model) modelCounts.set(r.winner_model, (modelCounts.get(r.winner_model) ?? 0) + 1);
    if (r.winner_agent) agentCounts.set(r.winner_agent, (agentCounts.get(r.winner_agent) ?? 0) + 1);
    if (r.frame_file) files.add(r.frame_file);
  }
  const topModelEntry = [...modelCounts.entries()].sort((a, b) => b[1] - a[1])[0];
  const topAgentEntry = [...agentCounts.entries()].sort((a, b) => b[1] - a[1])[0];

  return {
    fastestMs: fastest.total_elapsed_ms ?? null,
    fastestFile: fastest.frame_file ?? null,
    avgMs,
    topModel: topModelEntry ? { model: topModelEntry[0], wins: topModelEntry[1] } : null,
    topAgent: topAgentEntry ? { agent: topAgentEntry[0], wins: topAgentEntry[1] } : null,
    uniqueFiles: files.size,
  };
}
