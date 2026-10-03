"use client";

import { ArrowLeft, Play, Square } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { EventRow } from "@/components/Feed";
import { useStream } from "@/components/StreamProvider";
import { Button, Dot, Notice, StatRow, Tag } from "@/components/ui";
import { agentMeta } from "@/lib/agents";
import { fetchRun } from "@/lib/api";
import { clock, shortDate } from "@/lib/format";
import type { DeskEvent, RunSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";

/** A replay never waits longer than this between two steps, however long the real gap was. */
const LONGEST_PAUSE_MS = 700;

function seconds(from: string, to: string): number {
  return (new Date(to).getTime() - new Date(from).getTime()) / 1000;
}

function result(run: RunSummary): { label: string; status: "good" | "critical" | "neutral" } {
  if (run.ok === null) return { label: run.finished_at ? "Stopped" : "Running", status: "neutral" };
  return run.ok ? { label: "Done", status: "good" } : { label: "Failed", status: "critical" };
}

/** The trace: every event of the run in order, stamped with the time since the run began. */
function Trace({ events, start }: { events: DeskEvent[]; start: string }) {
  return (
    <ol className="divide-y divide-line">
      {events.map((event) => (
        <EventRow key={event.id} event={event} stamp={`+${seconds(start, event.ts).toFixed(2)} s`} />
      ))}
    </ol>
  );
}

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const { events: live } = useStream();
  // While the run is still going, reload as its events arrive.
  const newest = live.findLast((event) => event.run_id === id)?.id ?? 0;
  const { data, error, loading } = useApi(`run:${id}:${newest}`, (signal) => fetchRun(id, signal));

  // Replay: null shows the whole trace; a number shows that many steps and counts up.
  const [shown, setShown] = useState<number | null>(null);
  const events = useMemo(() => data?.events ?? [], [data]);
  const total = events.length;

  useEffect(() => {
    if (shown === null) return;
    if (shown >= events.length) {
      const done = setTimeout(() => setShown(null), 0);
      return () => clearTimeout(done);
    }
    const previous = events[shown - 1];
    const gap = previous ? seconds(previous.ts, events[shown].ts) * 1000 : 0;
    const timer = setTimeout(() => setShown(shown + 1), Math.min(LONGEST_PAUSE_MS, Math.max(60, gap)));
    return () => clearTimeout(timer);
  }, [shown, events]);

  const run = data?.run;
  const replaying = shown !== null;
  const visible = replaying ? events.slice(0, shown) : events;

  return (
    <div className="flex max-w-4xl flex-col gap-8">
      <Link href="/metrics" className="inline-flex items-center gap-2 self-start text-sm text-muted hover:text-fg">
        <ArrowLeft size={15} aria-hidden />
        Runs
      </Link>

      {error && <Notice tone="error">{error}</Notice>}
      {loading && !data && <p className="text-sm text-muted">Loading.</p>}

      {run && (
        <>
          <div>
            <p className="flex flex-wrap items-center gap-2 text-xs text-faint">
              <span className="inline-block size-2 rounded-full" style={{ background: agentMeta(run.agent).color }} aria-hidden />
              <span className="text-muted">{agentMeta(run.agent).name}</span>
              <span>
                {shortDate(run.started_at)} at {clock(run.started_at)}
              </span>
              {run.demo && <Tag>Recording</Tag>}
            </p>
            <h1 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">{run.title}</h1>
            <p className="mt-3 flex flex-wrap items-center gap-2 leading-relaxed text-muted">
              <Dot status={result(run).status} />
              {result(run).label}
              {run.text && <span>· {run.text}</span>}
            </p>
          </div>

          <div className="grid items-start gap-6 md:grid-cols-[minmax(0,1fr)_240px]">
            <section className="panel overflow-hidden" aria-label="Trace">
              <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
                <div>
                  <h2 className="font-display text-[15px] font-semibold tracking-tight">Trace</h2>
                  <p className="mt-0.5 text-xs text-faint">
                    {replaying ? `Step ${shown} of ${total}` : `${total} steps, including every request and call.`}
                  </p>
                </div>
                {replaying ? (
                  <Button onClick={() => setShown(null)}>
                    <Square size={13} aria-hidden />
                    Show everything
                  </Button>
                ) : (
                  <Button onClick={() => setShown(0)} disabled={total === 0}>
                    <Play size={14} aria-hidden />
                    Replay
                  </Button>
                )}
              </header>
              <Trace events={visible} start={run.started_at} />
            </section>

            <aside className="panel p-5" aria-label="What it used">
              <h2 className="eyebrow">What it used</h2>
              <dl className="mt-2 divide-y divide-line">
                <StatRow
                  label="GitHub requests"
                  value={run.usage.github_requests ?? 0}
                  note={run.usage.github_not_modified ? `${run.usage.github_not_modified} unchanged` : undefined}
                />
                <StatRow label="Model calls" value={run.usage.claude_calls ?? 0} />
                <StatRow label="Tokens in" value={run.usage.input_tokens ?? 0} />
                <StatRow label="Tokens out" value={run.usage.output_tokens ?? 0} />
                <StatRow label="Calls avoided" value={run.usage.calls_avoided ?? 0} />
                {run.finished_at && (
                  <StatRow label="Seconds" value={seconds(run.started_at, run.finished_at).toFixed(1)} />
                )}
              </dl>
              <p className="mt-3 text-xs leading-relaxed text-faint">
                Replay plays the stored steps back on this page. It asks GitHub and Claude for nothing.
              </p>
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
