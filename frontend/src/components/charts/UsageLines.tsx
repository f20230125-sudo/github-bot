"use client";

import { useState, type KeyboardEvent, type PointerEvent } from "react";
import { SHOWCASE } from "@/lib/showcase";
import type { UsageReading } from "@/lib/types";
import { ChartCard, DataTable, TooltipBox, useWidth, type Tip } from "./kit";

const HEIGHT = 240; // includes the band the time labels sit in
const MARGIN = { top: 16, right: 46, bottom: 26, left: 40 };
const SERIES = [
  { key: "session" as const, label: "5-hour limit", color: "var(--series-1)" },
  { key: "weekly" as const, label: "Weekly limit", color: "var(--series-2)" },
];
const LEGEND = SERIES.map((series) => ({ label: series.label, color: series.color, shape: "line" as const }));
const MAX_MARKERS = 24;

type Point = { t: number; percent: number };

function when(t: number, withDay: boolean): string {
  const date = new Date(t);
  const time = date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return withDay ? `${date.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}, ${time}` : time;
}

/**
 * Plan usage as Claude Code reported it, against the line where Patch stops calling Claude.
 * Each point is one reading. Patch only gets a reading when it talks to Claude Code, so the
 * points are as far apart as the calls were.
 */
export function UsageLines({
  readings,
  limits,
  now,
}: {
  readings: UsageReading[];
  limits: { session: number; weekly: number };
  now: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [active, setActive] = useState<number | null>(null);

  const points: Record<"session" | "weekly", Point[]> = { session: [], weekly: [] };
  for (const reading of readings) {
    points[reading.window]?.push({ t: new Date(reading.ts).getTime(), percent: reading.percent });
  }
  const times = [...new Set(readings.map((reading) => new Date(reading.ts).getTime()))].sort((a, b) => a - b);

  // Time runs from the first reading to now, with a little room on the left.
  const first = times[0] ?? now;
  const span = Math.max(now - first, 60 * 60 * 1000);
  const start = first - span * 0.04;
  // A time alone is ambiguous once the axis runs past midnight, so the day is added.
  const longSpan = new Date(start).toDateString() !== new Date(now).toDateString();
  const highest = Math.max(0, ...readings.map((reading) => reading.percent), limits.session, limits.weekly);
  const top = Math.min(100, Math.max(50, Math.ceil((highest + 5) / 10) * 10));
  const ticks = Array.from({ length: top / (top > 60 ? 20 : 10) + 1 }, (_, i) => i * (top > 60 ? 20 : 10));

  const plotWidth = Math.max(0, width - MARGIN.left - MARGIN.right);
  const plotHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const baseline = MARGIN.top + plotHeight;
  const x = (t: number) => MARGIN.left + ((t - start) / (now - start)) * plotWidth;
  const y = (percent: number) => baseline - (Math.min(percent, top) / top) * plotHeight;

  /** The reading a series had at a moment: the one taken then, or the latest before it. */
  const at = (series: Point[], t: number) => [...series].reverse().find((point) => point.t <= t);

  const stops = limits.session === limits.weekly
    ? [{ value: limits.session, label: `Stops at ${limits.session}%` }]
    : [
        { value: limits.session, label: `5-hour stop: ${limits.session}%` },
        { value: limits.weekly, label: `Weekly stop: ${limits.weekly}%` },
      ];

  // The newest value of each line is labelled at its end, unless the two would sit on top of each other.
  const ends = SERIES.map((series) => ({ ...series, last: points[series.key][points[series.key].length - 1] }));
  const labelled = ends.filter((end) => end.last);
  const collide = labelled.length === 2 && Math.abs(y(labelled[0].last.percent) - y(labelled[1].last.percent)) < 14;

  function nearest(clientX: number, left: number): number | null {
    if (!times.length) return null;
    const px = clientX - left;
    let best = 0;
    for (let i = 1; i < times.length; i++) {
      if (Math.abs(x(times[i]) - px) < Math.abs(x(times[best]) - px)) best = i;
    }
    return best;
  }

  function onMove(event: PointerEvent<SVGRectElement>) {
    const box = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (box) setActive(nearest(event.clientX, box.left));
  }

  function onKey(event: KeyboardEvent<SVGRectElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const step = event.key === "ArrowLeft" ? -1 : 1;
    setActive((current) => Math.min(times.length - 1, Math.max(0, (current ?? times.length - 1) + step)));
  }

  const tip: Tip | null =
    active === null || !times.length
      ? null
      : {
          x: x(times[active]),
          y: MARGIN.top,
          title: when(times[active], true),
          rows: SERIES.map((series) => {
            const reading = at(points[series.key], times[active]);
            return {
              label: series.label,
              value: reading ? `${Math.round(reading.percent)}%` : "no reading",
              color: series.color,
            };
          }),
        };

  const table = (
    <DataTable
      head={["Read at", "Limit", "Used"]}
      rows={[...readings]
        .reverse()
        .map((reading) => [
          when(new Date(reading.ts).getTime(), true),
          reading.window === "session" ? "5-hour" : "Weekly",
          `${Math.round(reading.percent)}%`,
        ])}
    />
  );

  return (
    <ChartCard
      title="Plan usage against the stop"
      subtitle="Each point is a reading from Claude Code. The figure covers your whole account, so your own Claude use counts too."
      legend={LEGEND}
      table={table}
      empty={
        readings.length
          ? null
          : SHOWCASE
            ? "No usage reading in this recording. Claude was switched off when it was taken."
            : "No usage reading in this period. The first one comes from Test Claude connection on the Setup page."
      }
    >
      <div ref={ref} className="relative" style={{ height: HEIGHT }}>
        {width > 0 && (
          <svg width={width} height={HEIGHT} role="img" aria-label="Plan usage over time for the 5-hour and weekly limits">
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
                  {tick}%
                </text>
              </g>
            ))}

            {/* The stop is a threshold, so it is dashed and named. Gridlines stay solid. */}
            {stops.map((stop, i) => (
              <g key={stop.label}>
                <line
                  x1={MARGIN.left}
                  x2={width - MARGIN.right}
                  y1={y(stop.value)}
                  y2={y(stop.value)}
                  stroke="var(--fg-muted)"
                  strokeDasharray="5 4"
                />
                <text x={MARGIN.left + 4} y={y(stop.value) + (i ? 14 : -6)} className="fill-muted text-[11px]">
                  {stop.label}
                </text>
              </g>
            ))}

            {[0, 0.5, 1].map((fraction) => {
              const t = start + (now - start) * fraction;
              const anchor = fraction === 0 ? "start" : fraction === 1 ? "end" : "middle";
              return (
                <text
                  key={fraction}
                  x={MARGIN.left + plotWidth * fraction}
                  y={HEIGHT - 8}
                  textAnchor={anchor}
                  className="fill-faint text-[11px]"
                >
                  {fraction === 1 ? "now" : when(t, longSpan)}
                </text>
              );
            })}

            {active !== null && times.length > 0 && (
              <line x1={x(times[active])} x2={x(times[active])} y1={MARGIN.top} y2={baseline} stroke="var(--line-strong)" />
            )}

            {SERIES.map((series) => {
              const line = points[series.key];
              if (!line.length) return null;
              const path = line.map((point, i) => `${i ? "L" : "M"}${x(point.t)},${y(point.percent)}`).join(" ");
              const marked = line.length <= MAX_MARKERS ? line : line.slice(-1);
              return (
                <g key={series.key}>
                  <path d={path} fill="none" stroke={series.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
                  {marked.map((point) => (
                    <circle
                      key={point.t}
                      cx={x(point.t)}
                      cy={y(point.percent)}
                      r={4}
                      fill={series.color}
                      stroke="var(--surface)"
                      strokeWidth={2}
                    />
                  ))}
                </g>
              );
            })}

            {!collide &&
              labelled.map((end) => (
                <text
                  key={end.key}
                  x={x(end.last.t) + 9}
                  y={y(end.last.percent) + 4}
                  className="fill-fg text-[11px] font-semibold"
                >
                  {Math.round(end.last.percent)}%
                </text>
              ))}

            {/* The pointer only has to be near a reading: the crosshair snaps to the closest one. */}
            <rect
              x={MARGIN.left}
              y={MARGIN.top}
              width={plotWidth}
              height={plotHeight}
              fill="transparent"
              tabIndex={0}
              role="img"
              aria-label="Usage readings. Use the left and right arrow keys to move between them."
              onPointerMove={onMove}
              onPointerLeave={() => setActive(null)}
              onFocus={() => setActive(times.length - 1)}
              onBlur={() => setActive(null)}
              onKeyDown={onKey}
            />
          </svg>
        )}
        <TooltipBox tip={tip} width={width} />
      </div>
    </ChartCard>
  );
}
