import { str, type DeskEvent, type EventType } from "./types";

/** One repository's report inside a run: its score line, then what is new or newly fixed. */
export type RepoBlock = { kind: "repo"; event: DeskEvent; findings: DeskEvent[] };
export type RunRow = { kind: "event"; event: DeskEvent } | RepoBlock;

export type RunGroup = {
  kind: "run";
  runId: string;
  agent: string;
  title: string;
  status: "running" | "done" | "failed";
  demo: boolean;
  githubRequests: number;
  notModified: number;
  claudeCalls: number;
  rows: RunRow[];
  lastId: number;
};

export type SingleItem = { kind: "single"; event: DeskEvent; lastId: number };
export type FeedItem = RunGroup | SingleItem;

/** Fold the flat event list into runs and loose events, newest first. */
export function buildFeed(events: DeskEvent[]): FeedItem[] {
  const runs = new Map<string, RunGroup>();
  const items: FeedItem[] = [];

  for (const event of events) {
    if (event.type === "agent.status") continue; // shown on the agent card, not in the feed

    if (!event.run_id) {
      items.push({ kind: "single", event, lastId: event.id });
      continue;
    }

    let run = runs.get(event.run_id);
    if (!run) {
      run = {
        kind: "run",
        runId: event.run_id,
        agent: event.agent,
        title: event.run_id,
        status: "running",
        demo: false,
        githubRequests: 0,
        notModified: 0,
        claudeCalls: 0,
        rows: [],
        lastId: event.id,
      };
      runs.set(event.run_id, run);
      items.push(run);
    }

    run.lastId = event.id;
    if (event.payload.demo === true) run.demo = true;

    switch (event.type) {
      case "run.started":
        run.title = str(event.payload, "title") ?? run.title;
        break;
      case "run.finished":
        run.status = event.payload.ok === false ? "failed" : "done";
        run.rows.push({ kind: "event", event });
        break;
      case "tool.result":
        // Every call to the outside world is counted here and listed on the run's own page.
        if (event.payload.kind === "github") {
          run.githubRequests += 1;
          if (event.payload.not_modified === true) run.notModified += 1;
        } else if (event.payload.kind === "claude") {
          run.claudeCalls += 1;
        }
        break;
      case "usage":
        break;
      case "run.step":
        if (event.payload.kind === "repo") run.rows.push({ kind: "repo", event, findings: [] });
        else run.rows.push({ kind: "event", event });
        break;
      case "finding": {
        const block = findRepoBlock(run.rows, event.repo);
        if (block) block.findings.push(event);
        else run.rows.push({ kind: "event", event });
        break;
      }
      default:
        if (event.type === "error") run.status = "failed";
        // Patch's answer to a message is shown in the chat thread. The feed keeps the question.
        if (event.type === "message" && event.payload.kind === "chat" && event.payload.from !== "you") break;
        run.rows.push({ kind: "event", event });
    }
  }

  return items.filter((item) => item.kind !== "run" || !isQuietChat(item)).sort((a, b) => b.lastId - a.lastId);
}

/** A message answered from stored data did no outside work. It lives in the chat thread, not the feed. */
function isQuietChat(run: RunGroup): boolean {
  return run.runId.startsWith("chat-") && run.githubRequests === 0 && run.claudeCalls === 0;
}

function findRepoBlock(rows: RunRow[], repo: string | null): RepoBlock | undefined {
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i];
    if (row.kind === "repo" && row.event.repo === repo) return row;
  }
  return undefined;
}

export type AgentView = { status: string; text: string; mood: string };

/** Latest status line an agent reported. */
export function agentView(events: DeskEvent[], agent: string): AgentView {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.agent === agent && e.type === "agent.status") {
      return {
        status: str(e.payload, "status") ?? "idle",
        text: str(e.payload, "text") ?? "",
        mood: str(e.payload, "mood") ?? "focused",
      };
    }
  }
  return { status: "idle", text: "Idle. Nothing to do.", mood: "focused" };
}

/** Id of the newest finished run. Pages reload their data when this changes. */
export function lastFinishedRun(events: DeskEvent[]): string {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === "run.finished") return events[i].run_id ?? "";
  }
  return "";
}

/** Id of the newest event of one type, or 0. Views reload their data when it changes. */
export function lastOfType(events: DeskEvent[], type: EventType): number {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === type) return events[i].id;
  }
  return 0;
}

/** Id of the newest message of one kind ("chat", "handoff"), or 0. */
export function lastMessage(events: DeskEvent[], kind: string): number {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === "message" && events[i].payload.kind === kind) return events[i].id;
  }
  return 0;
}

const PROPOSAL_EVENTS = new Set(["proposal.created", "proposal.resolved", "action.applied", "run.finished"]);

/** Id of the newest event that can change a proposal. Proposal views reload when this changes. */
export function lastProposalEvent(events: DeskEvent[]): number {
  for (let i = events.length - 1; i >= 0; i--) {
    if (PROPOSAL_EVENTS.has(events[i].type)) return events[i].id;
  }
  return 0;
}
