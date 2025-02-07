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

export function loadSnapshot(): Promise<Snapshot> {
  loading ??= (async () => {
    // The scheduled check commits a new snapshot whenever something changed. Reading it straight
    // from the repository shows it at once. The copy built into the site is the fallback.
    const fresh = FRESH_SNAPSHOT_URL ? await read(FRESH_SNAPSHOT_URL, { cache: "no-store" }) : null;
    const snapshot = fresh ?? (await read("/showcase/snapshot.json"));
    if (!snapshot) throw new Error("The snapshot is missing.");
    return snapshot;
  })();
  return loading;
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

/** The snapshot's answer for a request path, or undefined if the snapshot doesn't hold it. */
export async function showcaseGet(path: string): Promise<unknown> {
  const snapshot = await loadSnapshot();
  const answer = snapshot.routes[path] ?? snapshot.routes[decodeURIComponent(path)];
  return answer === undefined ? undefined : upTo(path, answer);
}

// -- the scheduled check ----------------------------------------------------------------------

export type ScheduledCheck = { at: string; ok: boolean; url: string };

const CHECK_CACHE = "desk-last-check";
const CHECK_CACHE_MS = 10 * 60 * 1000;

/**
 * When the scheduled check last ran, asked from GitHub's public API. A check that finds nothing
 * commits nothing, so this is the only place that knows it ran. Visitors get 60 such requests
 * an hour from GitHub, so the answer is kept for ten minutes.
 */
export async function fetchScheduledCheck(): Promise<ScheduledCheck | null> {
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
 * is the owner's click, made on GitHub while signed in to GitHub: this site never holds a token.
 * Returns null when the content is too long to travel in an address.
 */
export function newFileUrl(repo: string, branch: string, path: string, content: string): string | null {
  const url = `https://github.com/${repo}/new/${branch}?filename=${encodeURIComponent(path)}&value=${encodeURIComponent(content)}`;
  return url.length <= LONGEST_URL ? url : null;
}

export function editFileUrl(repo: string, branch: string, path: string): string {
  return `https://github.com/${repo}/edit/${branch}/${path}`;
}
