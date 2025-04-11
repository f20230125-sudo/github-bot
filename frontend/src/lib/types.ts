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

/** One thing a post may state, with an address when there is something to open. */
export type BriefFact = { label: string; value: string; url: string | null };

/** Pitch's reading of a note as things stand now: is there enough for a post, and what may it state? */
export type Brief = {
  ready: boolean;
  angle: string;
  angle_label: string;
  /** "Enough for a post." or "Not yet.", in Pitch's words. */
  verdict: string;
  facts: BriefFact[];
  /** Why the post has to wait. Empty when there is enough. */
  missing: string[];
  /** What would make the post better, without holding it back. */
  wanted: string[];
};

/** A note Patch left for Pitch about something worth a post. */
export type Handoff = {
  id: number;
  ts: string;
  repo: string | null;
  from: string;
  to: string;
  topic: string;
  text: string;
  data: Record<string, unknown>;
  /** What ties a post to this note. Missing in a snapshot taken before Pitch could write. */
  thread?: string;
  /** Missing in a snapshot taken before Pitch joined the desk. */
  brief?: Brief | null;
};

/** One version of a post, in one tone. */
export type PostVariant = { tone: string; label: string; text: string };

/** A post Pitch drafted. It exists on the working desk only: never on the view-only copy. */
export type Post = {
  id: number;
  /** The note it was written for. */
  thread: string;
  repo: string | null;
  status: "draft" | "posted" | "dismissed";
  title: string;
  angle_label: string | null;
  /** "claude": one model call wrote it. "template": built from the facts, no model. */
  source: "claude" | "template";
  variants: PostVariant[];
  /** Other opening lines for the first version. */
  hooks: string[];
  /** Versions that were thrown away, and why. */
  notes: string[];
  picture: { path: string; url: string } | null;
  /** Your version, once you have copied or posted it. */
  text: string | null;
  tone: string | null;
  reason: string | null;
  created_at: string;
  updated_at: string;
};

/** A public project of yours with no note yet: something you can ask Pitch to write about. */
export type PickableRepo = { repo: string; name: string; ready: boolean; reason: string | null };

export type PitchPosts = {
  posts: Post[];
  tone: string | null;
  /** Notes a draft is being written for right now, by id. */
  writing: number[];
  /** Why Claude can't be asked, going by the last usage reading. Null if it can. */
  claude_off: string | null;
  /** The usage will be read again, at no cost, when a draft is asked for: "off" may no longer hold. */
  claude_rechecks?: boolean;
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

/** An agent's personality, as written in its persona.toml. */
export type Voice = {
  summary: string;
  rules: string[];
  opinions: string[];
  quirks: string[];
  banned: string[];
  max_chars: number;
  max_sentences: number;
};

export type AgentSheet = AgentInfo & {
  paused: boolean;
  persona: Voice;
  moods: Record<string, string>;
  writes: string[];
  never: string[];
  lessons: Lesson[];
  lesson_limit: number;
};

export type PitchSheet = AgentInfo & {
  paused: boolean;
  persona: Voice;
  moods: Record<string, string>;
  /** What Pitch does today, and what it has no way to do. */
  does: string[];
  never: string[];
  /** The tone you chose for your posts. Null until you have picked one. */
  tone?: string | null;
  tones?: Record<string, { label: string; how: string }>;
  lessons?: Lesson[];
  lesson_limit?: number;
};

export function str(payload: Record<string, unknown>, key: string): string | undefined {
  const v = payload[key];
  return typeof v === "string" ? v : undefined;
}

export function num(payload: Record<string, unknown>, key: string): number | undefined {
  const v = payload[key];
  return typeof v === "number" ? v : undefined;
}
