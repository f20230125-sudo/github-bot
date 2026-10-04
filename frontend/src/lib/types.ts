export const EVENT_TYPES = [
  "run.started",
  "run.step",
  "tool.call",
  "tool.result",
  "finding",
  "proposal.created",
  "proposal.resolved",
  "action.applied",
  "message",
  "usage",
  "agent.status",
  "run.finished",
  "error",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

export type DeskEvent = {
  id: number;
  ts: string;
  agent: string;
  type: EventType;
  run_id: string | null;
  repo: string | null;
  payload: Record<string, unknown>;
};

export type Severity = "critical" | "high" | "medium" | "low" | "info";
export const SEVERITIES: Severity[] = ["critical", "high", "medium", "low", "info"];

export type Portfolio = {
  repos: number;
  scored: number;
  score: number | null;
  findings: number;
  counts: Partial<Record<Severity, number>>;
};

export type RepoCard = {
  full_name: string;
  name: string;
  owner: string;
  kind: "project" | "profile" | "placeholder" | "skipped";
  score: number | null;
  counts: Partial<Record<Severity, number>>;
  description: string | null;
  html_url: string | null;
  homepage: string | null;
  language: string | null;
  topics: string[];
  license: string | null;
  private: boolean;
  stars: number;
  pushed_at: string | null;
  ci_state: "success" | "failure" | "pending" | "neutral" | null;
  synced_at: string;
};

export type FindingView = {
  check: string;
  severity: Severity;
  title: string;
  detail: string;
  fix: "metadata" | "file" | "manual" | null;
  weight: number;
  text: string;
};

export type RepoDetail = {
  repo: RepoCard;
  findings: FindingView[];
  history: { ts: string; score: number }[];
  readme: { path: string | null; chars: number };
  files: number;
};

export type Setup = {
  github: { configured: boolean; user: string; mode: "token" | "public" };
  dry_run: boolean;
};

export type ProposalStatus = "pending" | "approved" | "rejected" | "applied" | "failed" | "superseded";

export type ActionResult = {
  repo: string;
  action: string;
  ok: boolean;
  dry_run: boolean;
  text: string;
  value?: string | string[];
  needed?: string;
  url?: string;
  number?: number;
};

export type ProposalCard = {
  id: number;
  agent: string;
  kind: "metadata_sweep" | "pull_request";
  repo: string | null;
  title: string;
  summary: string;
  status: ProposalStatus;
  items: number;
  created_at: string;
  updated_at: string;
  decision: { action: string; at: string; reason?: string; edits?: unknown[] } | null;
  result: {
    dry_run?: boolean;
    at: string;
    actions?: ActionResult[];
    url?: string;
    number?: number;
    stale?: boolean;
    /** You fixed it on GitHub yourself, so Patch dropped the proposal. */
    fixed_elsewhere?: boolean;
  } | null;
};

export type SweepItem = {
  repo: string;
  description: string | null;
  topics: string[] | null;
  current_description: string | null;
  current_topics: string[];
  enabled: boolean;
};

export type FileItem = {
  path: string;
  content: string;
  previous: string | null;
  findings: string[];
  reason: string;
  source: "template" | "claude";
  enabled: boolean;
  note: string | null;
  is_new: boolean;
  diff: string[];
};

export type ProposalDetail = ProposalCard & {
  payload: { items?: SweepItem[]; notes?: string[]; files?: FileItem[]; repo?: string; base_branch?: string };
};

export type UsageWindow = {
  percent: number | null;
  resets_at: number | null;
  minutes_old: number | null;
  source: string | null;
};

export type ClaudeStatus = {
  auth: { installed: boolean; signed_in: boolean; method: string | null; plan: string | null; ok: boolean; problem: string | null };
  guard: {
    allowed: boolean;
    code: string;
    reason: string;
    limits: { session: number; weekly: number };
    fixed_allowance: number;
    usage_command: string | null;
    windows: { session: UsageWindow; weekly: UsageWindow };
  };
  models?: { writing: string; small: string };
};

export type ClaudeTest = ClaudeStatus & {
  usage: { ok: boolean; answered_locally?: boolean; text?: string; tokens?: number; reports?: unknown[]; error?: string } | null;
  structured: {
    ok: boolean;
    skipped?: boolean;
    via?: "schema" | "text" | null;
    model?: string;
    tokens?: number;
    input_tokens?: number;
    output_tokens?: number;
    ms?: number;
    error?: string;
  } | null;
};

export type PolicyMode = "ask" | "auto" | "never";

export type Policy = {
  dry_run: boolean;
  merge_after_approval: boolean;
  kinds: Record<string, PolicyMode>;
  kind_labels: Record<string, string>;
};

/** The last time Patch asked GitHub what changed. */
export type LastCheck = {
  at: string;
  changed: boolean;
  requests: number;
  status: number | null;
  free: boolean;
  remaining: number | null;
  error: string | null;
};

export type WatchInfo = {
  enabled: boolean;
  interval: number;
  /** When the next check is due, in seconds since the epoch. */
  next_at: number | null;
  last: LastCheck | null;
};

export type AgentInfo = {
  id: string;
  name: string;
  role: string;
  /** False for a seat reserved for an agent that doesn't exist yet. */
  hired: boolean;
  mood?: string;
  status?: { status: string; text: string; mood: string } | null;
  watch?: WatchInfo;
};

export type DeskState = { paused: boolean; current: string | null; agents: AgentInfo[] };

export type ChatMessage = {
  id: number;
  ts: string;
  run_id: string | null;
  from: string;
  text: string;
  points: string[];
  /** "rules": answered from stored data. "claude": a model call was made. */
  source: "rules" | "claude" | null;
  note: string | null;
};

export type Handoff = {
  id: number;
  ts: string;
  repo: string | null;
  from: string;
  to: string;
  topic: string;
  text: string;
  data: Record<string, unknown>;
};

/** One day's totals across everything the desk did. */
export type DayStats = {
  day: string;
  runs: number;
  checks: number;
  idle_checks: number;
  github_requests: number;
  github_not_modified: number;
  claude_calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  calls_avoided: number;
  handoffs: number;
};

export type JobUsage = { job: string; calls: number; tokens_in: number; tokens_out: number };

export type UsageReading = { ts: string; window: "session" | "weekly"; percent: number };

export type Metrics = {
  days: DayStats[];
  today: DayStats;
  totals: Omit<DayStats, "day">;
  by_job: JobUsage[];
  decisions: {
    approved: number;
    rejected: number;
    /** Approved, but only after you changed something. */
    edited: number;
    pending: number;
    rate: number | null;
    by_day: { day: string; approved: number; rejected: number }[];
  };
  usage: ClaudeStatus["guard"] & { readings: UsageReading[] };
};

export type RunSummary = {
  run_id: string;
  agent: string;
  job: string | null;
  title: string;
  demo: boolean;
  started_at: string;
  finished_at: string | null;
  /** null while the run is going, or if it was cut off. */
  ok: boolean | null;
  text: string | null;
  usage: Record<string, number>;
};

export type Lesson = {
  id: number;
  agent: string;
  text: string;
  /** "rejection" and "edit" were learned from a decision. "you" was written by you. */
  source: "rejection" | "edit" | "you";
  proposal_id: number | null;
  active: boolean;
  created_at: string;
};

export type AgentSheet = AgentInfo & {
  paused: boolean;
  persona: {
    summary: string;
    rules: string[];
    opinions: string[];
    quirks: string[];
    banned: string[];
    max_chars: number;
    max_sentences: number;
  };
  moods: Record<string, string>;
  writes: string[];
  never: string[];
  lessons: Lesson[];
  lesson_limit: number;
};

export function str(payload: Record<string, unknown>, key: string): string | undefined {
  const v = payload[key];
  return typeof v === "string" ? v : undefined;
}

export function num(payload: Record<string, unknown>, key: string): number | undefined {
  const v = payload[key];
  return typeof v === "number" ? v : undefined;
}
