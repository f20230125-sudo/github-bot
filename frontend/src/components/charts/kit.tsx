"use client";

import { ChartNoAxesColumn, Table2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

/** The width of an element, kept up to date, so a chart can be drawn at its real size. */
export function useWidth<T extends HTMLElement>(): [RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

/** Round axis values from zero up to at least `max`: 0, 10, 20, 30. Whole numbers only by default. */
export function niceTicks(max: number, count = 4, whole = true): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  let step = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= raw) ?? 10 * magnitude;
  if (whole) step = Math.max(1, Math.round(step));
  const steps = Math.ceil(max / step);
  return Array.from({ length: steps + 1 }, (_, i) => i * step);
}

const COMPACT = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const GROUPED = new Intl.NumberFormat("en");

/** 1,284 stays as it is; 12,900 becomes 12.9K. */
export function compact(n: number): string {
  return n < 10_000 ? GROUPED.format(n) : COMPACT.format(n);
}

export function grouped(n: number): string {
  return GROUPED.format(n);
}

/** "2026-10-03" -> "3 Oct" */
export function dayLabel(day: string): string {
  return new Date(`${day}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export type LegendItem = { label: string; color: string; shape: "bar" | "line" };

/** Which colour is which series. The swatch copies the mark: a block for bars, a stroke for lines. */
export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {items.map((item) => (
        <li key={item.label} className="inline-flex items-center gap-2">
          <span
            className={item.shape === "bar" ? "inline-block size-2.5 rounded-[2px]" : "inline-block h-0.5 w-3.5 rounded-full"}
            style={{ background: item.color }}
            aria-hidden
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

export type Tip = {
  x: number;
  y: number;
  title: string;
  rows: { label: string; value: string; color?: string }[];
};

/** The hover readout. Values lead and labels follow. It repeats what the table view holds. */
export function TooltipBox({ tip, width }: { tip: Tip | null; width: number }) {
  if (!tip) return null;
  const flip = tip.x > width / 2; // keep the box inside the chart
  return (
    <div
      className="pointer-events-none absolute z-10 min-w-36 rounded-xl border border-line-strong bg-surface-2 px-3 py-2 text-xs shadow-lg"
      style={{ top: tip.y, left: flip ? undefined : tip.x + 12, right: flip ? width - tip.x + 12 : undefined }}
      aria-hidden
    >
      <p className="text-faint">{tip.title}</p>
      {tip.rows.map((row) => (
        <p key={row.label} className="mt-1 flex items-center gap-2 whitespace-nowrap">
          {row.color && (
            <span className="inline-block h-0.5 w-3 shrink-0 rounded-full" style={{ background: row.color }} />
          )}
          <span className="font-semibold text-fg">{row.value}</span>
          <span className="text-muted">{row.label}</span>
        </p>
      ))}
    </div>
  );
}

type CardProps = {
  title: string;
  subtitle?: string;
  legend?: LegendItem[];
  /** The same numbers as a table: every chart has one, so nothing depends on hovering or on colour. */
  table: ReactNode;
  /** Shown instead of the chart when there is nothing to draw. */
  empty?: string | null;
  children: ReactNode;
};

/** The frame every chart sits in: title, legend, and a switch between the chart and its table. */
export function ChartCard({ title, subtitle, legend, table, empty, children }: CardProps) {
  const [showTable, setShowTable] = useState(false);
  const Icon = showTable ? ChartNoAxesColumn : Table2;
  return (
    <figure className="panel flex min-w-0 flex-col gap-4 p-5">
      <div className="flex items-start justify-between gap-3">
        <figcaption className="min-w-0">
          <h2 className="font-display text-[15px] font-semibold tracking-tight">{title}</h2>
          {subtitle && <p className="mt-1 text-xs leading-relaxed text-faint">{subtitle}</p>}
        </figcaption>
        {!empty && (
          <button
            type="button"
            onClick={() => setShowTable((value) => !value)}
            aria-pressed={showTable}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-xs text-muted transition hover:border-line-strong hover:text-fg"
          >
            <Icon size={12} aria-hidden />
            {showTable ? "Chart" : "Table"}
          </button>
        )}
      </div>
      {empty ? (
        <p className="py-6 text-sm leading-relaxed text-muted">{empty}</p>
      ) : showTable ? (
        <div className="max-h-72 overflow-auto">{table}</div>
      ) : (
        <>
          {legend && legend.length > 1 && <Legend items={legend} />}
          {children}
        </>
      )}
    </figure>
  );
}

/** A plain table for a chart's numbers. Number columns line up on the right. */
export function DataTable({ head, rows }: { head: string[]; rows: (string | number)[][] }) {
  return (
    <table className="w-full text-left text-sm">
      <thead>
        <tr className="border-b border-line text-xs text-faint">
          {head.map((cell, i) => (
            <th key={cell} scope="col" className={`py-2 font-normal ${i ? "pl-4 text-right" : ""}`}>
              {cell}
            </th>
          ))}
        </tr>
      </thead>
      <tbody className="divide-y divide-line">
        {rows.map((row, r) => (
          <tr key={r}>
            {row.map((cell, i) => (
              <td key={i} className={`py-2 ${i ? "tabular pl-4 text-right" : "text-muted"}`}>
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
