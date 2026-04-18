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
