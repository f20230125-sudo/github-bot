import type { DeskEvent } from "./types";

/**
 * True in the public, view-only build. That build has no backend: every page reads one exported
 * snapshot (public/showcase/snapshot.json, written by `python -m app.showcase`), and nothing on
 * it can change anything.
 */
export const SHOWCASE = process.env.NEXT_PUBLIC_SHOWCASE === "1";

export const REPO_URL = "https://github.com/f20230125-sudo/github-bot";

export type Snapshot = { exported_at: string; events: DeskEvent[]; routes: Record<string, unknown> };

let loading: Promise<Snapshot> | null = null;

export function loadSnapshot(): Promise<Snapshot> {
  loading ??= fetch("/showcase/snapshot.json").then((response) => {
    if (!response.ok) throw new Error("The snapshot is missing.");
    return response.json() as Promise<Snapshot>;
  });
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
