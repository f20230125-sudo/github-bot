import type {
  AgentSheet,
  ChatMessage,
  ClaudeStatus,
  ClaudeTest,
  DeskEvent,
  DeskState,
  Handoff,
  Lesson,
  Metrics,
  Policy,
  Portfolio,
  ProposalCard,
  ProposalDetail,
  RepoCard,
  RepoDetail,
  RunSummary,
  Setup,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8010").replace(/\/$/, "");

export type Health = {
  status: string;
  dry_run: boolean;
  github_configured: boolean;
  github_user: string;
  events: number;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "The API isn't reachable. Start it with dev.ps1.");
  }
  if (!res.ok) throw new ApiError(res.status, await errorDetail(res));
  return res.json() as Promise<T>;
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg;
  } catch {
    /* fall through to the status text */
  }
  return `${res.status} ${res.statusText}`;
}

/** Anything that changes state carries this header; the API refuses writes without it. */
const WRITE = { "X-Desk-Request": "1" };
const WRITE_JSON = { ...WRITE, "Content-Type": "application/json" };

export const fetchHealth = (signal?: AbortSignal) => request<Health>("/api/health", { signal });

export const playDemo = (speed = 1) =>
  request<{ started: boolean; speed: number }>(`/api/demo/replay?speed=${speed}`, { method: "POST", headers: WRITE });

export const startAudit = (force = false) =>
  request<{ queued: boolean }>(`/api/jobs/audit?force=${force}`, { method: "POST", headers: WRITE });

export const fetchRepos = (signal?: AbortSignal) =>
  request<{ portfolio: Portfolio; repos: RepoCard[] }>("/api/repos", { signal });

export const fetchRepo = (owner: string, name: string, signal?: AbortSignal) =>
  request<RepoDetail>(`/api/repos/${encodeURIComponent(owner)}/${encodeURIComponent(name)}`, { signal });

export const fetchSetup = (signal?: AbortSignal) => request<Setup>("/api/setup", { signal });

export const saveGithubToken = (token: string) =>
  request<{ user: string }>("/api/setup/github-token", {
    method: "PUT",
    headers: WRITE_JSON,
    body: JSON.stringify({ token }),
  });

export const removeGithubToken = () =>
  request<{ configured: boolean }>("/api/setup/github-token", { method: "DELETE", headers: WRITE });

// -- proposals --------------------------------------------------------------------------------

export const startDraft = () => request<{ queued: boolean }>("/api/jobs/draft", { method: "POST", headers: WRITE });

export const fetchProposals = (status: string, signal?: AbortSignal) =>
  request<{ proposals: ProposalCard[] }>(`/api/proposals?status=${status}`, { signal });

export const fetchProposal = (id: number, signal?: AbortSignal) =>
  request<ProposalDetail>(`/api/proposals/${id}`, { signal });

export type ProposalEdits = {
  items?: { repo: string; enabled: boolean; description?: string; topics?: string[] }[];
  files?: { path: string; enabled: boolean; content?: string }[];
};

export const approveProposal = (id: number, edits: ProposalEdits) =>
  request<ProposalCard>(`/api/proposals/${id}/approve`, {
    method: "POST",
    headers: WRITE_JSON,
    body: JSON.stringify(edits),
  });

export const rejectProposal = (id: number, reason: string) =>
  request<ProposalCard>(`/api/proposals/${id}/reject`, {
    method: "POST",
    headers: WRITE_JSON,
    body: JSON.stringify({ reason }),
  });

export const applyProposal = (id: number) =>
  request<{ queued: boolean }>(`/api/proposals/${id}/apply`, { method: "POST", headers: WRITE });

// -- the desk: agents, pause, chat, handoffs --------------------------------------------------

export const fetchDesk = (signal?: AbortSignal) => request<DeskState>("/api/agents", { signal });

/** The kill switch. Pausing stops the job in progress and refuses new work until you resume. */
export const setPaused = (paused: boolean) =>
  request<{ paused: boolean; changed: boolean }>("/api/pause", {
    method: "PUT",
    headers: WRITE_JSON,
    body: JSON.stringify({ paused }),
  });

/** Send Patch a message. The answer arrives on the event stream. */
export const sendChat = (text: string) =>
  request<{ accepted: boolean }>("/api/chat", { method: "POST", headers: WRITE_JSON, body: JSON.stringify({ text }) });

export const fetchChat = (signal?: AbortSignal) =>
  request<{ messages: ChatMessage[]; busy: boolean }>("/api/chat?limit=30", { signal });

export const fetchHandoffs = (signal?: AbortSignal) => request<{ handoffs: Handoff[] }>("/api/handoffs", { signal });

// -- looking back: metrics, runs, Patch's page ------------------------------------------------

export const fetchMetrics = (days: number, signal?: AbortSignal) =>
  request<Metrics>(`/api/metrics?days=${days}`, { signal });

export const fetchRuns = (limit: number, signal?: AbortSignal) =>
  request<{ runs: RunSummary[] }>(`/api/runs?limit=${limit}`, { signal });

export const fetchRun = (runId: string, signal?: AbortSignal) =>
  request<{ run: RunSummary; events: DeskEvent[] }>(`/api/runs/${encodeURIComponent(runId)}`, { signal });

export const fetchSheet = (signal?: AbortSignal) => request<AgentSheet>("/api/agents/patch", { signal });

export const addLesson = (text: string) =>
  request<Lesson>("/api/lessons", { method: "POST", headers: WRITE_JSON, body: JSON.stringify({ text }) });

export const updateLesson = (id: number, changes: { text?: string; active?: boolean }) =>
  request<Lesson>(`/api/lessons/${id}`, { method: "PUT", headers: WRITE_JSON, body: JSON.stringify(changes) });

export const deleteLesson = (id: number) =>
  request<{ deleted: boolean }>(`/api/lessons/${id}`, { method: "DELETE", headers: WRITE });

// -- Claude and policy ------------------------------------------------------------------------

export const fetchClaude = (signal?: AbortSignal) => request<ClaudeStatus>("/api/claude", { signal });

export const testClaude = () => request<ClaudeTest>("/api/claude/test", { method: "POST", headers: WRITE });

export const fetchPolicy = (signal?: AbortSignal) => request<Policy>("/api/policy", { signal });

export const updatePolicy = (changes: Partial<Pick<Policy, "dry_run" | "merge_after_approval" | "kinds">>) =>
  request<Policy>("/api/policy", { method: "PUT", headers: WRITE_JSON, body: JSON.stringify(changes) });
