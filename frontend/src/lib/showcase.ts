import type { DeskEvent } from "./types";

/**
 * True in the public, view-only build. That build has no backend: every page reads one exported
 * snapshot (public/showcase/snapshot.json, written by `python -m app.showcase` or by the
 * scheduled check, `python -m app.cloud`), and nothing on it can change anything.
 */
export const SHOWCASE = process.env.NEXT_PUBLIC_SHOWCASE === "1";

/** The repository this site is built from, as owner/name. */
export const REPO = process.env.NEXT_PUBLIC_REPO || "f20230125-sudo/github-bot";
export const REPO_URL = `https://github.com/${REPO}`;
/** The scheduled check on GitHub. Its owner can start one by hand from this page. */
export const WORKFLOW_URL = `${REPO_URL}/actions/workflows/patch-watch.yml`;
/** How often the scheduled check runs. Keep in step with .github/workflows/patch-watch.yml. */
export const CHECK_EVERY = "six hours";

/** Where the newest committed snapshot can be read without waiting for a new deployment. */
const FRESH_SNAPSHOT_URL = process.env.NEXT_PUBLIC_SNAPSHOT_URL || "";

export type Snapshot = { exported_at: string; digest?: string; events: DeskEvent[]; routes: Record<string, unknown> };

let loading: Promise<Snapshot> | null = null;

async function read(url: string, init?: RequestInit): Promise<Snapshot | null> {
  try {
    const response = await fetch(url, init);
    return response.ok ? ((await response.json()) as Snapshot) : null;
  } catch {
    return null;
  }
}

/**
 * The newest snapshot there is. The scheduled check commits a new one whenever something changed,
 * so it is read from the repository: through the site's own server when that is connected to
 * GitHub (exact), else from GitHub's file cache (can lag a commit by a few minutes). The copy
 * built into the site is the last resort. `version` makes the address new, to get past caches.
 */
async function newest(version?: string): Promise<Snapshot | null> {
  const viaServer = await read(`/live/snapshot${version ? `?v=${encodeURIComponent(version)}` : ""}`);
  if (viaServer?.routes) return viaServer;
  const fromGitHub = FRESH_SNAPSHOT_URL ? await read(FRESH_SNAPSHOT_URL, { cache: "no-store" }) : null;
  return fromGitHub?.routes ? fromGitHub : null;
}

const SEEN_KEY = "desk-snapshot-seen";

/** When the newest snapshot this browser has shown was written, or "" if it has shown none. */
function seen(): string {
  try {
    return localStorage.getItem(SEEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function remember(snapshot: Snapshot): void {
  try {
    localStorage.setItem(SEEN_KEY, snapshot.exported_at);
  } catch {
    /* without storage, a page can briefly show the copy from before a check */
  }
}

export function loadSnapshot(): Promise<Snapshot> {
  loading ??= (async () => {
    let snapshot = await newest();
    // The site's server shares one answer between visitors for some seconds, and GitHub's file
    // cache keeps one for minutes. Either can hand over the copy from before a check whose result
    // this browser has already shown. Then ask again, at an address no cache has an older copy of.
    const known = seen();
    if (snapshot && known && Date.parse(snapshot.exported_at) < Date.parse(known)) {
      snapshot = (await newest(known)) ?? snapshot;
    }
    snapshot ??= await read("/showcase/snapshot.json");
    if (!snapshot) throw new Error("The snapshot is missing.");
    remember(snapshot);
    return snapshot;
  })();
  return loading;
}

/**
 * After a check: whether the repository now holds a different snapshot from the one on screen.
 * If it does, the next page load in this browser will not settle for an older copy.
 */
export async function snapshotChanged(version: string): Promise<boolean> {
  const [shown, latest] = await Promise.all([loadSnapshot(), newest(version)]);
  if (latest === null || (latest.digest ?? latest.exported_at) === (shown.digest ?? shown.exported_at)) return false;
  remember(latest);
  return true;
}

/**
 * How far the replay has got. While the recording plays, lists that grew during the real run
 * (approvals, chat) only show what had happened by that point. null means the recording is over.
 * It starts at the beginning, so a page that asks before the recording starts sees nothing yet.
 */
let cursor: { id: number; ts: string } | null = { id: 0, ts: "" };

export function setCursor(event: { id: number; ts: string } | null): void {
  cursor = event;
}

type Dated = { id: number; created_at?: string };

function upTo(path: string, answer: unknown): unknown {
  if (cursor === null) return answer;
  const at = cursor;
  if (path.startsWith("/api/chat")) {
    const { messages } = answer as { messages: Dated[] };
    return { messages: messages.filter((message) => message.id <= at.id), busy: false };
  }
  if (path.startsWith("/api/proposals?")) {
    const { proposals } = answer as { proposals: Dated[] };
    return { proposals: proposals.filter((proposal) => (proposal.created_at ?? "") <= at.ts) };
  }
  if (path === "/api/handoffs") {
    const { handoffs } = answer as { handoffs: Dated[] };
    return { handoffs: handoffs.filter((handoff) => handoff.id <= at.id) };
  }
  return answer;
}

/**
 * The snapshot's answer for a request path, or undefined if the snapshot doesn't hold it.
 * `whole` gives the answer as it stood at the end, whatever point the replay has reached: for a
 * page that is not part of what the recording plays back.
 */
export async function showcaseGet(path: string, whole = false): Promise<unknown> {
  const snapshot = await loadSnapshot();
  const answer = snapshot.routes[path] ?? snapshot.routes[decodeURIComponent(path)];
  return answer === undefined || whole ? answer : upTo(path, answer);
}

// -- the scheduled check ----------------------------------------------------------------------

export type ScheduledCheck = { at: string; ok: boolean; url: string };

/** What the site's own server says about the check: see app/live/check/route.ts. */
export type CheckStatus = {
  configured: boolean;
  state?: "idle" | "running" | "unknown";
  at?: string | null;
  ok?: boolean | null;
  url?: string | null;
  run_id?: number | null;
  started?: boolean;
  reason?: "running" | "recent" | "refused" | "unreachable";
  since?: string;
  retry_in?: number;
};

/** Ask the site's server how the check is going. null when the site has no server side at all. */
export async function checkStatus(): Promise<CheckStatus | null> {
  try {
    const response = await fetch("/live/check", { cache: "no-store" });
    return (await response.json()) as CheckStatus;
  } catch {
    return null;
  }
}

/** Ask the site's server to start a check now. */
export async function startCheck(): Promise<CheckStatus | null> {
  try {
    const response = await fetch("/live/check", { method: "POST", cache: "no-store" });
    return (await response.json()) as CheckStatus;
  } catch {
    return null;
  }
}

/** Told to anything showing "last check" when a check started from this page has finished. */
export const CHECKED_EVENT = "desk-checked";

const CHECK_CACHE = "desk-last-check";
const CHECK_CACHE_MS = 10 * 60 * 1000;

/**
 * When the scheduled check last ran. A check that finds nothing commits nothing, so the snapshot
 * can't say. The site's own server knows, when it is connected to GitHub. Otherwise GitHub's
 * public API is asked from the browser: visitors get 60 such requests an hour, so that answer is
 * kept for ten minutes.
 */
export async function fetchScheduledCheck(): Promise<ScheduledCheck | null> {
  const server = await checkStatus();
  if (server?.configured) {
    return server.state === "idle" && server.at && server.url
      ? { at: server.at, ok: server.ok === true, url: server.url }
      : null;
  }
  try {
    const kept = JSON.parse(sessionStorage.getItem(CHECK_CACHE) ?? "null") as { saved: number; check: ScheduledCheck } | null;
    if (kept && Date.now() - kept.saved < CHECK_CACHE_MS) return kept.check;
  } catch {
    /* no storage, or nothing kept: ask GitHub */
  }
  try {
    const response = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/patch-watch.yml/runs?per_page=1&status=completed`,
      { headers: { Accept: "application/vnd.github+json" } },
    );
    if (!response.ok) return null;
    const run = (await response.json()).workflow_runs?.[0];
    if (!run) return null;
    const check = { at: run.updated_at as string, ok: run.conclusion === "success", url: run.html_url as string };
    try {
      sessionStorage.setItem(CHECK_CACHE, JSON.stringify({ saved: Date.now(), check }));
    } catch {
      /* fine without it */
    }
    return check;
  } catch {
    return null;
  }
}

// -- applying a suggestion on GitHub ----------------------------------------------------------

/** Longer than this and GitHub may refuse the address, so the content is copied instead. */
const LONGEST_URL = 7000;

/**
 * GitHub's own "new file" page with the name and the content already filled in. Committing there
 * is the owner's click, made on GitHub while signed in to GitHub: this site holds nothing that
 * can write to a repository. Returns null when the content is too long to travel in an address.
 */
export function newFileUrl(repo: string, branch: string, path: string, content: string): string | null {
  const url = `https://github.com/${repo}/new/${branch}?filename=${encodeURIComponent(path)}&value=${encodeURIComponent(content)}`;
  return url.length <= LONGEST_URL ? url : null;
}

export function editFileUrl(repo: string, branch: string, path: string): string {
  return `https://github.com/${repo}/edit/${branch}/${path}`;
}
