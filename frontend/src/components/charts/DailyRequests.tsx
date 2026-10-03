"use client";

import { useState } from "react";
import type { DayStats } from "@/lib/types";
import { ChartCard, DataTable, dayLabel, grouped, niceTicks, TooltipBox, useWidth, type Tip } from "./kit";

const HEIGHT = 230; // includes the band the day labels sit in
const MARGIN = { top: 22, right: 8, bottom: 26, left: 36 };
const UNCHANGED = "var(--series-1)";
const FULL = "var(--series-2)";
const LEGEND = [
  { label: "Unchanged (304)", color: UNCHANGED, shape: "bar" as const },
  { label: "Answered in full", color: FULL, shape: "bar" as const },
];

/** A column segment: square at the bottom, and rounded at the top when it is the top of its stack. */
function segment(x: number, top: number, width: number, height: number, cap: boolean): string {
  const r = cap ? Math.min(4, height, width / 2) : 0;
  return `M${x},${top + height} V${top + r} Q${x},${top} ${x + r},${top} H${x + width - r} Q${x + width},${top} ${x + width},${top + r} V${top + height} Z`;
}

/**
 * GitHub requests per day, split into answers that came back unchanged and answers in full.
 * An unchanged answer is what a conditional request is for: with a token it costs no quota.
 */
export function DailyRequests({ days }: { days: DayStats[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);

  const totals = days.map((day) => day.github_requests);
  const highest = Math.max(0, ...totals);
  const ticks = niceTicks(highest);
  const top = ticks[ticks.length - 1];
  const plotWidth = Math.max(0, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const band = days.length ? plotWidth / days.length : 0;
  const barWidth = Math.max(3, Math.min(24, band - 6));
  const baseline = MARGIN.top + plotHeight;
  const y = (value: number) => baseline - (value / top) * plotHeight;
  const labelEvery = Math.max(1, Math.ceil(48 / Math.max(band, 1)));
  const peak = totals.indexOf(highest);

  const tip: Tip | null =
    active === null
      ? null
      : {
          x: MARGIN.left + band * (active + 0.5),
          y: MARGIN.top,
          title: `${dayLabel(days[active].day)} · ${grouped(totals[active])} requests`,
          rows: [
            { label: "unchanged", value: grouped(days[active].github_not_modified), color: UNCHANGED },
            {
              label: "answered in full",
              value: grouped(totals[active] - days[active].github_not_modified),
              color: FULL,
            },
          ],
        };

  const table = (
    <DataTable
      head={["Day", "Requests", "Unchanged", "Answered in full"]}
      rows={[...days]
        .reverse()
        .map((day) => [
          dayLabel(day.day),
          grouped(day.github_requests),
          grouped(day.github_not_modified),
          grouped(day.github_requests - day.github_not_modified),
        ])}
    />
  );

  return (
    <ChartCard
      title="GitHub requests per day"
      subtitle="Every check asks GitHub whether anything changed. Unchanged answers carry no data and, with a token, cost no quota."
      legend={LEGEND}
      table={table}
      empty={highest === 0 ? "No requests in this period." : null}
    >
      <div ref={ref} className="relative" style={{ height: HEIGHT }}>
        {width > 0 && (
          <svg width={width} height={HEIGHT} role="img" aria-label="GitHub requests per day, unchanged and answered in full">
            {ticks.map((tick) => (
              <g key={tick}>
                <line
                  x1={MARGIN.left}
                  x2={width - MARGIN.right}
                  y1={y(tick)}
                  y2={y(tick)}
                  stroke={tick === 0 ? "var(--line-strong)" : "var(--line)"}
                />
                <text x={MARGIN.left - 8} y={y(tick) + 4} textAnchor="end" className="tabular fill-faint text-[11px]">
                  {grouped(tick)}
                </text>
              </g>
            ))}

            {days.map((day, i) => {
              const x = MARGIN.left + band * i + (band - barWidth) / 2;
              const unchanged = day.github_not_modified;
              const full = day.github_requests - unchanged;
              const lower = y(unchanged);
              const upper = y(day.github_requests);
              // A 2px gap in the surface colour separates the two segments. No outline is drawn.
              const gap = unchanged > 0 && full > 0 ? 2 : 0;
              const lit = active === i ? { filter: "brightness(1.2)" } : undefined;
              return (
                <g key={day.day}>
                  {active === i && (
                    <rect x={MARGIN.left + band * i} y={MARGIN.top} width={band} height={plotHeight} fill="var(--surface-2)" />
                  )}
                  {unchanged > 0 && (
                    <path d={segment(x, lower, barWidth, baseline - lower, full === 0)} fill={UNCHANGED} style={lit} />
                  )}
                  {full > 0 && (
                    <path
                      d={segment(x, upper, barWidth, Math.max(1, lower - upper - gap), true)}
                      fill={FULL}
                      style={lit}
                    />
                  )}
                  {/* One direct label, on the busiest day. The axis, the tooltip and the table carry the rest. */}
                  {i === peak && (
                    <text x={x + barWidth / 2} y={upper - 6} textAnchor="middle" className="fill-fg text-[11px] font-semibold">
                      {grouped(day.github_requests)}
                    </text>
                  )}
                  {(days.length - 1 - i) % labelEvery === 0 && (
                    <text x={x + barWidth / 2} y={HEIGHT - 8} textAnchor="middle" className="fill-faint text-[11px]">
                      {dayLabel(day.day)}
                    </text>
                  )}
                  {/* The whole band is the hover and focus target, not just the painted bar. */}
                  <rect
                    x={MARGIN.left + band * i}
                    y={MARGIN.top}
                    width={band}
                    height={plotHeight}
                    fill="transparent"
                    tabIndex={0}
                    role="img"
                    aria-label={`${dayLabel(day.day)}: ${day.github_requests} requests, ${unchanged} unchanged`}
                    onPointerEnter={() => setActive(i)}
                    onPointerLeave={() => setActive(null)}
                    onFocus={() => setActive(i)}
                    onBlur={() => setActive(null)}
                  />
                </g>
              );
            })}
          </svg>
        )}
        <TooltipBox tip={tip} width={width} />
      </div>
    </ChartCard>
  );
}
