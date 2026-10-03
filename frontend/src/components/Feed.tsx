import {
  AlertTriangle,
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleCheck,
  FileDiff,
  MessageSquare,
  Play,
  Sigma,
  TrendingDown,
  TrendingUp,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { agentMeta } from "@/lib/agents";
import type { FeedItem, RepoBlock, RunGroup } from "@/lib/feed";
import { clock, plural, shortRepo, statusColor } from "@/lib/format";
import { num, str, type DeskEvent, type EventType, type Severity } from "@/lib/types";
import { Dot, severityColor, SeverityCounts, Tag } from "./ui";

const ICONS: Partial<Record<EventType, LucideIcon>> = {
  "run.started": Play,
  "tool.result": ArrowUpRight,
  usage: Sigma,
  "run.step": ChevronRight,
  finding: AlertTriangle,
  "proposal.created": FileDiff,
  "proposal.resolved": CircleCheck,
  "action.applied": Check,
  message: MessageSquare,
  "run.finished": Check,
  error: XCircle,
};

const RUN_STATUS = {
  running: { label: "Running", status: "good" },
  done: { label: "Done", status: null },
  failed: { label: "Failed", status: "critical" },
} as const;

function severityOf(event: DeskEvent): Severity {
  return (str(event.payload, "severity") as Severity | undefined) ?? "info";
}

/** The colour of a row's icon. Only the icon is coloured; the text beside it stays in ink. */
function markColor(event: DeskEvent): string | undefined {
  if (event.type === "finding") {
    return event.payload.status === "resolved" ? statusColor("good") : severityColor(severityOf(event));
  }
  if (event.type === "error") return statusColor("critical");
  if (event.type === "run.finished" && event.payload.ok === false) return statusColor("critical");
  if (event.type === "tool.result" && (event.payload.ok === false || (num(event.payload, "status") ?? 0) >= 400)) {
    return statusColor("critical");
  }
  if (event.type === "action.applied") {
    if (event.payload.ok === false) return statusColor("critical");
    return event.payload.dry_run === true ? undefined : statusColor("good");
  }
  return undefined;
}

function EventBody({ event }: { event: DeskEvent }) {
  const p = event.payload;
  const text = str(p, "text") ?? "";

  if (event.type === "finding") {
    const resolved = p.status === "resolved";
    return (
      <p className="text-sm leading-relaxed">
        <span className="mr-2 text-[11px] font-medium uppercase tracking-wide text-faint">
          {resolved ? "fixed" : severityOf(event)}
        </span>
        <span className="text-muted">{text}</span>
      </p>
    );
  }

  if (event.type === "proposal.created" || event.type === "proposal.resolved") {
    const id = num(p, "proposal_id");
    const title = str(p, "title") ?? "Proposal";
    const decision = str(p, "decision");
    return (
      <div className="text-sm leading-relaxed">
        <p className="font-medium">
          {decision && <span className="mr-2 font-normal capitalize text-muted">{decision}:</span>}
          {id !== undefined ? (
            <Link href={`/proposals/${id}`} className="underline underline-offset-4">
              {title}
            </Link>
          ) : (
            title
          )}
          {event.repo && <span className="ml-2 font-mono text-xs font-normal text-muted">{shortRepo(event.repo)}</span>}
        </p>
        <p className="text-muted">{decision ? text : str(p, "summary")}</p>
      </div>
    );
  }

  if (event.type === "action.applied") {
    const url = str(p, "url");
    return (
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm leading-relaxed">
        {event.repo && <span className="font-mono text-xs">{shortRepo(event.repo)}</span>}
        <span className="text-muted">{text}</span>
        {p.dry_run === true && <Tag>Rehearsal</Tag>}
        {url && (
          <a href={url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-4">
            Open
          </a>
        )}
      </p>
    );
  }

  // The next three only appear on a run's own page: the feed counts them instead of listing them.
  if (event.type === "tool.result" && p.kind === "github") {
    return (
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm leading-relaxed">
        <span className="break-all font-mono text-xs">
          {str(p, "method")} {str(p, "path")}
        </span>
        <span className="tabular font-mono text-xs text-muted">{num(p, "status")}</span>
        {p.not_modified === true && <Tag>Unchanged</Tag>}
        <span className="tabular text-xs text-faint">
          {num(p, "ms")} ms{num(p, "remaining") !== undefined && ` · ${num(p, "remaining")} requests left this hour`}
        </span>
      </p>
    );
  }

  if (event.type === "tool.result" && p.kind === "claude") {
    const tokensIn = (num(p, "input_tokens") ?? 0) + (num(p, "cache_read_tokens") ?? 0) + (num(p, "cache_write_tokens") ?? 0);
    return (
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm leading-relaxed">
        <span>Claude call: {str(p, "job")}</span>
        {str(p, "model") && <span className="font-mono text-xs text-muted">{str(p, "model")}</span>}
        <span className="tabular text-xs text-faint">
          {tokensIn} tokens in, {num(p, "output_tokens") ?? 0} out · {((num(p, "ms") ?? 0) / 1000).toFixed(1)} s
        </span>
        {str(p, "error") && <span className="text-muted">{str(p, "error")}</span>}
      </p>
    );
  }

  if (event.type === "run.started") {
    return <p className="text-sm leading-relaxed text-muted">Started: {str(p, "title") ?? "a run"}.</p>;
  }

  if (event.type === "usage") {
    return (
      <p className="text-sm leading-relaxed text-muted">
        Totals: {plural(num(p, "github_requests") ?? 0, "GitHub request")},{" "}
        {plural(num(p, "claude_calls") ?? 0, "model call")}, {plural(num(p, "calls_avoided") ?? 0, "call")} avoided.
      </p>
    );
  }

  // Which repository a line is about, when it is about one.
  const about = event.repo && <span className="mr-2 font-mono text-xs text-fg">{shortRepo(event.repo)}</span>;

  if (event.type === "message" && p.kind === "chat") {
    const mine = p.from === "you";
    const points = Array.isArray(p.points) ? (p.points as string[]) : [];
    return (
      <div className="text-sm leading-relaxed">
        <p className={mine ? "text-muted" : ""}>
          <span className="mr-2 text-xs text-faint">{mine ? "You" : agentMeta(event.agent).name}</span>
          {text}
        </p>
        {points.length > 0 && (
          <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-5 text-muted marker:text-faint">
            {points.map((point, i) => (
              <li key={i}>{point}</li>
            ))}
          </ul>
        )}
        {str(p, "note") && <p className="mt-1 text-xs text-faint">{str(p, "note")}</p>}
      </div>
    );
  }

  if (event.type === "message") {
    const to = str(p, "to");
    return (
      <p className="text-sm leading-relaxed">
        {to && <span className="mr-2 text-xs text-faint">to {agentMeta(to).name}</span>}
        {about}
        <span>{text}</span>
      </p>
    );
  }

  return (
    <p className="text-sm leading-relaxed text-muted">
      {about}
      {text || str(p, "message") || event.type}
    </p>
  );
}

/** One event as a row. `stamp` replaces the clock time, for a trace that counts from the run's start. */
export function EventRow({ event, inset = false, stamp }: { event: DeskEvent; inset?: boolean; stamp?: string }) {
  const resolved = event.type === "finding" && event.payload.status === "resolved";
  const refused = event.type === "action.applied" && event.payload.ok === false;
  const failedCall = event.type === "tool.result" && (event.payload.ok === false || (num(event.payload, "status") ?? 0) >= 400);
  const Icon = resolved ? CircleCheck : refused || failedCall ? XCircle : (ICONS[event.type] ?? ChevronRight);
  return (
    <li className={`flex gap-3 py-3 pr-5 ${inset ? "pl-12" : "pl-5"}`}>
      <Icon size={15} className="mt-1 shrink-0 text-faint" style={{ color: markColor(event) }} aria-hidden />
      <div className="min-w-0 flex-1">
        <EventBody event={event} />
      </div>
      <time className="tabular mt-0.5 hidden shrink-0 font-mono text-[11px] text-faint sm:block" dateTime={event.ts}>
        {stamp ?? clock(event.ts)}
      </time>
    </li>
  );
}

/** One repository's result: a line you can open to see what is new or newly fixed. */
function RepoRow({ block }: { block: RepoBlock }) {
  const p = block.event.payload;
  const score = num(p, "score");
  const previous = num(p, "previous_score");
  const counts = (p.counts ?? {}) as Partial<Record<Severity, number>>;
  const delta = score !== undefined && previous !== undefined && previous !== score ? score - previous : null;
  const DeltaIcon = delta !== null && delta > 0 ? TrendingUp : TrendingDown;

  return (
    <li>
      <details className="group">
        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 py-3 pl-5 pr-5 hover:bg-surface-2 [&::-webkit-details-marker]:hidden">
          <ChevronRight
            size={15}
            className="shrink-0 text-faint transition-transform group-open:rotate-90"
            aria-hidden
          />
          <span className="min-w-0 truncate font-mono text-xs">{shortRepo(block.event.repo ?? "")}</span>
          <span className="tabular font-mono text-xs text-muted">{score ?? "not scored"}</span>
          {delta !== null && (
            <span className="tabular inline-flex items-center gap-1 font-mono text-xs text-muted">
              <DeltaIcon size={13} style={{ color: statusColor(delta > 0 ? "good" : "critical") }} aria-hidden />
              {delta > 0 ? `+${delta}` : delta}
            </span>
          )}
          <span className="ml-auto">
            <SeverityCounts counts={counts} />
          </span>
        </summary>
        <ul className="border-t border-line bg-sunken">
          {block.findings.length ? (
            block.findings.map((finding) => <EventRow key={finding.id} event={finding} inset />)
          ) : (
            <li className="py-3 pl-12 pr-5 text-sm text-faint">Nothing new since the last audit.</li>
          )}
        </ul>
      </details>
    </li>
  );
}

function RunCard({ run }: { run: RunGroup }) {
  const meta = agentMeta(run.agent);
  const status = RUN_STATUS[run.status];
  return (
    <article className="panel rise-in overflow-hidden">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-5 py-3.5">
        <span className="inline-block size-2 rounded-full" style={{ background: meta.color }} aria-hidden />
        <span className="text-sm text-muted">{meta.name}</span>
        <h3 className="font-display text-[15px] font-semibold tracking-tight">
          {/* The run's own page lists every request and call it made. */}
          <Link href={`/runs/${run.runId}`} className="underline-offset-4 hover:underline">
            {run.title}
          </Link>
        </h3>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          {status.status && <Dot status={status.status} className={run.status === "running" ? "live-dot" : ""} />}
          {status.label}
        </span>
        {run.demo && <Tag>Recording</Tag>}
        <span className="tabular ml-auto font-mono text-[11px] text-faint">
          {plural(run.githubRequests, "GitHub request")}
          {run.notModified > 0 && ` (${run.notModified} unchanged)`} · {plural(run.claudeCalls, "model call")}
        </span>
      </header>
      <ol className="divide-y divide-line">
        {run.rows.map((row) =>
          row.kind === "repo" ? (
            <RepoRow key={row.event.id} block={row} />
          ) : (
            <EventRow key={row.event.id} event={row.event} />
          ),
        )}
      </ol>
    </article>
  );
}

export function Feed({ items }: { items: FeedItem[] }) {
  return (
    <div className="flex flex-col gap-4" role="log" aria-label="Agent activity" aria-live="off">
      {items.map((item) =>
        item.kind === "run" ? (
          <RunCard key={`run-${item.runId}`} run={item} />
        ) : (
          <article key={`event-${item.event.id}`} className="panel rise-in">
            <ol>
              <EventRow event={item.event} />
            </ol>
          </article>
        ),
      )}
    </div>
  );
}
