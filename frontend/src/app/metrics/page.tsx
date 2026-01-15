"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { DailyRequests } from "@/components/charts/DailyRequests";
import { JobBars } from "@/components/charts/JobBars";
import { compact, grouped } from "@/components/charts/kit";
import { UsageLines } from "@/components/charts/UsageLines";
import { UsageMeter } from "@/components/ClaudePanel";
import { useStream } from "@/components/StreamProvider";
import { Dot, Notice, Tag } from "@/components/ui";
import { fetchMetrics, fetchRuns } from "@/lib/api";
import { lastFinishedRun } from "@/lib/feed";
import { clock, plural, shortDate } from "@/lib/format";
import type { RunSummary } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { useNow } from "@/lib/useNow";

const RANGES = [7, 14, 30];

function StatTile({ label, value, children }: { label: string; value: ReactNode; children?: ReactNode }) {
  return (
    <div className="panel p-5">
      <p className="text-xs text-faint">{label}</p>
      {/* Proportional figures: a standalone number, not a column. */}
      <p className="mt-2 text-3xl font-semibold leading-none">{value}</p>
      {children && <p className="mt-3 text-xs leading-relaxed text-muted">{children}</p>}
    </div>
  );
}

function runResult(run: RunSummary): { label: string; status: "good" | "critical" | "neutral" } {
  if (run.ok === null) return { label: run.finished_at ? "Stopped" : "Running", status: "neutral" };
  return run.ok ? { label: "Done", status: "good" } : { label: "Failed", status: "critical" };
}

function RunsTable({ runs }: { runs: RunSummary[] }) {
  return (
    <section className="panel overflow-hidden" aria-label="Runs">
      <div className="px-5 pt-5">
        <h2 className="font-display text-[15px] font-semibold tracking-tight">Runs</h2>
        <p className="mt-1 text-xs leading-relaxed text-faint">
          Every piece of work, newest first. Open one to see each request and call it made.
        </p>
      </div>
      {runs.length ? (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-y border-line text-xs text-faint">
                <th scope="col" className="px-5 py-2 font-normal">Started</th>
                <th scope="col" className="py-2 font-normal">Job</th>
                <th scope="col" className="py-2 font-normal">Result</th>
                <th scope="col" className="py-2 text-right font-normal">GitHub requests</th>
                <th scope="col" className="px-5 py-2 text-right font-normal">Model calls</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {runs.map((run) => {
                const result = runResult(run);
                return (
                  <tr key={run.run_id} className="hover:bg-surface-2">
                    <td className="tabular whitespace-nowrap px-5 py-2.5 font-mono text-xs text-muted">
                      {shortDate(run.started_at)} {clock(run.started_at)}
                    </td>
                    <td className="py-2.5">
                      <Link href={`/runs/${run.run_id}`} className="underline underline-offset-4">
                        {run.title}
                      </Link>
                      {run.demo && (
                        <span className="ml-2">
                          <Tag>Recording</Tag>
                        </span>
                      )}
                    </td>
                    <td className="py-2.5">
                      <span className="inline-flex items-center gap-2 text-muted">
                        <Dot status={result.status} />
                        {result.label}
                      </span>
                    </td>
                    <td className="tabular py-2.5 text-right">{grouped(run.usage.github_requests ?? 0)}</td>
                    <td className="tabular px-5 py-2.5 text-right">{grouped(run.usage.claude_calls ?? 0)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="px-5 py-6 text-sm text-muted">No runs in this period.</p>
      )}
    </section>
  );
}

export default function MetricsPage() {
  const { events, status } = useStream();
  const [days, setDays] = useState(14);
  const now = useNow();
  const version = `${status}:${lastFinishedRun(events)}`;
  const { data, error } = useApi(`metrics:${days}:${version}`, (signal) => fetchMetrics(days, signal));
  const { data: runData } = useApi(`runs:${version}`, (signal) => fetchRuns(100, signal));

  // While a new range loads, the page keeps showing the previous one, dimmed. No layout jump.
  const stale = data !== null && data.days.length !== days;
  const totals = data?.totals;
  const decisions = data?.decisions;
  const since = data?.days[0]?.day ?? "";
  const runs = (runData?.runs ?? []).filter((run) => run.started_at.slice(0, 10) >= since);
  const decided = decisions ? decisions.approved + decisions.rejected : 0;

  return (
    <div className="flex flex-col gap-8">
      <div>
        <p className="eyebrow">Metrics</p>
        <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight sm:text-4xl">What the work cost.</h1>
      </div>

      {/* One filter row, above everything it scopes. */}
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Period">
        {RANGES.map((range) => (
          <button
            key={range}
            type="button"
            onClick={() => setDays(range)}
            aria-pressed={days === range}
            className={`rounded-full px-3.5 py-1.5 text-sm transition ${
              days === range ? "bg-fg text-bg" : "border border-line-strong text-muted hover:text-fg"
            }`}
          >
            Last {range} days
          </button>
        ))}
      </div>

      {error && <Notice tone="error">{error}</Notice>}

      {data && totals && decisions && (
        <div className={`flex flex-col gap-6 transition-opacity ${stale ? "opacity-50" : ""}`}>
          <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-label="Totals">
            <div className="panel p-6 sm:col-span-2">
              <p className="text-xs text-faint">Model calls avoided</p>
              <p className="mt-2 text-6xl font-semibold leading-none">{compact(totals.calls_avoided)}</p>
              <p className="mt-4 max-w-prose text-sm leading-relaxed text-muted">
                Times a rule or a template did a job a model would otherwise have been asked to do: checking a
                repository, writing a license, answering a lookup. Patch made {plural(totals.claude_calls, "model call")}{" "}
                in the same period.
              </p>
            </div>
            <StatTile label="Model calls" value={compact(totals.claude_calls)}>
              {compact(totals.input_tokens)} tokens in, {compact(totals.output_tokens)} out.
              {totals.cache_read_tokens > 0 && ` ${compact(totals.cache_read_tokens)} of the input was read from cache.`}
            </StatTile>
            <StatTile label="GitHub requests" value={compact(totals.github_requests)}>
              {totals.github_requests
                ? `${compact(totals.github_not_modified)} came back unchanged.`
                : "None yet."}
            </StatTile>
            <StatTile label="Checks" value={compact(totals.checks)}>
              {totals.checks
                ? `${compact(totals.idle_checks)} found nothing and stayed out of the feed.`
                : "Patch has not asked GitHub what changed yet."}
            </StatTile>
            <StatTile label="Proposals approved" value={decided ? `${decisions.approved} of ${decided}` : "None yet"}>
              {decided > 0 && `${decisions.rate}% accepted. `}
              {decisions.edited > 0 && `${plural(decisions.edited, "was", "were")} edited first. `}
              {decisions.pending > 0 ? `${decisions.pending} waiting for you.` : "Nothing is waiting."}
            </StatTile>
            <div className="panel p-5 sm:col-span-2">
              <p className="text-xs text-faint">Plan usage now</p>
              <div className="mt-1 divide-y divide-line">
                <UsageMeter label="5-hour limit" window={data.usage.windows.session} limit={data.usage.limits.session} />
                <UsageMeter label="Weekly limit" window={data.usage.windows.weekly} limit={data.usage.limits.weekly} />
              </div>
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <DailyRequests days={data.days} />
            {now !== null && <UsageLines readings={data.usage.readings} limits={data.usage.limits} now={now} />}
          </div>
          <JobBars jobs={data.by_job} />
          <RunsTable runs={runs} />
        </div>
      )}
    </div>
  );
}
