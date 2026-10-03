"use client";

import { useState } from "react";
import { plural } from "@/lib/format";
import type { JobUsage } from "@/lib/types";
import { ChartCard, DataTable, grouped, TooltipBox, useWidth, type Tip } from "./kit";

const JOB_LABEL: Record<string, string> = {
  sweep: "Descriptions and topics",
  readme: "README drafts",
  chat: "Answers in chat",
  lesson: "Lessons from decisions",
  "connection test": "Connection test",
  "usage check": "Usage check",
};
const ROW = 34;
const ROW_STACKED = 52;

export function jobLabel(job: string): string {
  return JOB_LABEL[job] ?? job;
}

/**
 * Tokens used by each kind of job: tokens are what count against the plan. One series, so every
 * bar wears the same colour and there is no legend. The number at the bar's tip carries the value.
 */
export function JobBars({ jobs }: { jobs: JobUsage[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);
  const rows = jobs
    .map((job) => ({ ...job, tokens: job.tokens_in + job.tokens_out }))
    .sort((a, b) => b.tokens - a.tokens || a.job.localeCompare(b.job));
  const most = Math.max(1, ...rows.map((row) => row.tokens));
  // On a narrow screen the name goes above its bar, so the bar keeps room to show its length.
  const stacked = width > 0 && width < 520;
  const rowHeight = stacked ? ROW_STACKED : ROW;

  const tip: Tip | null =
    active === null
      ? null
      : {
          x: width * 0.45,
          y: active * rowHeight + rowHeight - 4,
          title: jobLabel(rows[active].job),
          rows: [
            { label: "tokens in", value: grouped(rows[active].tokens_in) },
            { label: "tokens out", value: grouped(rows[active].tokens_out) },
            { label: rows[active].calls === 1 ? "call" : "calls", value: grouped(rows[active].calls) },
          ],
        };

  const table = (
    <DataTable
      head={["Job", "Calls", "Tokens in", "Tokens out"]}
      rows={rows.map((row) => [jobLabel(row.job), grouped(row.calls), grouped(row.tokens_in), grouped(row.tokens_out)])}
    />
  );

  return (
    <ChartCard
      title="Tokens by job"
      subtitle="What Claude was used for. Audits, checks and template files never appear here: they use no model."
      table={table}
      empty={rows.length ? null : "No model calls in this period."}
    >
      <div ref={ref} className="relative">
        <ul>
          {rows.map((row, i) => (
            <li
              key={row.job}
              tabIndex={0}
              aria-label={`${jobLabel(row.job)}: ${row.tokens} tokens in ${plural(row.calls, "call")}`}
              onPointerEnter={() => setActive(i)}
              onPointerLeave={() => setActive(null)}
              onFocus={() => setActive(i)}
              onBlur={() => setActive(null)}
              className={
                stacked
                  ? "flex flex-col justify-center gap-1 rounded-lg"
                  : "grid grid-cols-[minmax(0,12rem)_minmax(0,1fr)] items-center gap-3 rounded-lg"
              }
              style={{ height: rowHeight }}
            >
              <span className="truncate text-sm text-muted">{jobLabel(row.job)}</span>
              <span className="flex items-center gap-2">
                <span
                  className="block h-3.5 rounded-r-[4px]"
                  style={{
                    // Room is kept at the end of the track for the number at the bar's tip.
                    width: `max(2px, calc((100% - 8.5rem) * ${row.tokens / most}))`,
                    background: "var(--series-1)",
                    filter: active === i ? "brightness(1.2)" : undefined,
                  }}
                />
                <span className="tabular whitespace-nowrap text-xs">
                  <span className="font-semibold">{grouped(row.tokens)}</span>
                  <span className="ml-1.5 text-faint">in {plural(row.calls, "call")}</span>
                </span>
              </span>
            </li>
          ))}
        </ul>
        <TooltipBox tip={tip} width={width} />
      </div>
    </ChartCard>
  );
}
