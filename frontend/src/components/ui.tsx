import { AlertTriangle } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { plural, scoreStatus, SEVERITY_STATUS, statusColor, type Status } from "@/lib/format";
import { SEVERITIES, type Severity } from "@/lib/types";

/** A small coloured mark. It always sits beside a word or number, so colour is never the only signal. */
export function Dot({ status, className = "" }: { status: Status; className?: string }) {
  return (
    <span
      className={`inline-block size-2 shrink-0 rounded-full ${className}`}
      style={{ background: statusColor(status) }}
      aria-hidden
    />
  );
}

export function severityColor(severity: Severity): string {
  return statusColor(SEVERITY_STATUS[severity]);
}

/** "1 high · 3 medium · 1 low", each with its mark. Info-level notes are advice, so they aren't counted. */
export function SeverityCounts({ counts }: { counts: Partial<Record<Severity, number>> }) {
  const shown = SEVERITIES.filter((s) => s !== "info" && (counts[s] ?? 0) > 0);
  if (!shown.length) {
    const notes = counts.info ?? 0;
    return <span className="text-xs text-faint">{notes ? plural(notes, "note") : "Nothing to fix"}</span>;
  }
  return (
    <span className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
      {shown.map((severity) => (
        <span key={severity} className="inline-flex items-center gap-1.5">
          <Dot status={SEVERITY_STATUS[severity]} />
          {counts[severity]} {severity}
        </span>
      ))}
    </span>
  );
}

export function findingTotal(counts: Partial<Record<Severity, number>>): string {
  const n = SEVERITIES.filter((s) => s !== "info").reduce((sum, s) => sum + (counts[s] ?? 0), 0);
  return plural(n, "finding");
}

/** A score out of 100 as a thin meter. The track is a dimmer step of the fill's own colour. */
export function ScoreMeter({ score }: { score: number | null }) {
  if (score === null) return null;
  const color = statusColor(scoreStatus(score));
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-r-[3px]"
      style={{ background: `color-mix(in oklab, ${color} 22%, var(--surface))` }}
      role="meter"
      aria-label="Health score"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={score}
    >
      <div className="h-full rounded-r-[3px]" style={{ width: `${score}%`, background: color }} />
    </div>
  );
}

/** One number with its label, as a row. Used in a list, so the values line up in a column. */
export function StatRow({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2">
      <dt className="text-sm text-muted">
        {label}
        {note && <span className="ml-2 text-xs text-faint">{note}</span>}
      </dt>
      <dd className="tabular text-lg font-semibold">{value}</dd>
    </div>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return <span className="eyebrow rounded-full border border-line px-2 py-0.5">{children}</span>;
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" };

export function Button({ variant = "ghost", className = "", type = "button", ...props }: ButtonProps) {
  const look = variant === "primary" ? "bg-fg text-bg hover:opacity-90" : "border border-line-strong hover:bg-surface-2";
  return (
    <button
      type={type}
      className={`inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${look} ${className}`}
      {...props}
    />
  );
}

/** A message in a box. Errors carry a warning mark; the text itself stays in ink. */
export function Notice({ tone = "info", children }: { tone?: "info" | "error"; children: ReactNode }) {
  const error = tone === "error";
  return (
    <p
      className={`flex items-start gap-3 rounded-2xl border border-line bg-surface-2 px-5 py-3 text-sm ${error ? "text-fg" : "text-muted"}`}
      role={error ? "alert" : undefined}
    >
      {error && <AlertTriangle size={16} className="mt-0.5 shrink-0 text-critical" aria-hidden />}
      <span>{children}</span>
    </p>
  );
}

export function InlineError({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm" role="alert">
      <AlertTriangle size={14} className="shrink-0 text-critical" aria-hidden />
      {children}
    </span>
  );
}
