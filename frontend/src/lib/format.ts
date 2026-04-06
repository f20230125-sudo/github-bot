import type { Severity } from "./types";

export function plural(n: number, word: string, pluralWord?: string): string {
  return `${n} ${n === 1 ? word : (pluralWord ?? `${word}s`)}`;
}

export function clock(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

export function shortDate(ts: string): string {
  return new Date(ts).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}

/** How long ago something happened: "just now", "4 min ago", "3 h ago", "2 d ago". */
export function ago(ts: string, now: number): string {
  const seconds = Math.max(0, (now - new Date(ts).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

/** How long until a moment given in seconds since the epoch: "any moment", "in about 4 min". */
export function until(epochSeconds: number, now: number): string {
  const seconds = epochSeconds - now / 1000;
  if (seconds < 45) return "any moment";
  if (seconds < 3600) return `in about ${Math.max(1, Math.round(seconds / 60))} min`;
  return `in about ${Math.round(seconds / 3600)} h`;
}

/**
 * When a usage limit resets, given in seconds since the epoch: "at 21:20" if that is today,
 * else "on 11 Oct at 16:00". Only used on data that arrives after the page is live.
 */
export function resetTime(epochSeconds: number): string {
  const moment = new Date(epochSeconds * 1000);
  const time = moment.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  if (moment.toDateString() === new Date().toDateString()) return `at ${time}`;
  return `on ${moment.toLocaleDateString([], { day: "numeric", month: "short" })} at ${time}`;
}

/** "f20230125-sudo/Quant-Trading-copilot" -> "Quant-Trading-copilot" */
export function shortRepo(fullName: string): string {
  return fullName.split("/").pop() ?? fullName;
}

export type Status = "good" | "warning" | "serious" | "critical" | "neutral";

/** The CSS colour for a status mark. Marks only: the text beside them stays in ink. */
export function statusColor(status: Status): string {
  return `var(--status-${status})`;
}

export const SEVERITY_STATUS: Record<Severity, Status> = {
  critical: "critical",
  high: "serious",
  medium: "warning",
  low: "neutral",
  info: "neutral",
};

/** Which band a health score falls in. The number is always shown beside the mark. */
export function scoreStatus(score: number): Status {
  if (score >= 85) return "good";
  if (score >= 60) return "neutral";
  if (score >= 40) return "warning";
  return "critical";
}
