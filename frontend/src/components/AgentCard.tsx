"use client";

import { ago, until } from "@/lib/format";
import type { WatchInfo } from "@/lib/types";
import { useNow } from "@/lib/useNow";
import { Face } from "./Face";

type Props = {
  name: string;
  role: string;
  color: string;
  status: string;
  text: string;
  mood: string;
  /** A seat reserved for an agent that has not been built yet. */
  reserved?: boolean;
  /** The scheduled check, for an agent that watches something. */
  watch?: WatchInfo;
  paused?: boolean;
};

const STATUS_LABEL: Record<string, string> = {
  working: "Working",
  waiting: "Waiting for you",
  idle: "Idle",
  paused: "Paused",
};

/** When the agent last looked at GitHub, what it found, and when it will look again. */
function WatchLines({ watch, paused }: { watch: WatchInfo; paused: boolean }) {
  const now = useNow();
  if (now === null) return null;
  const last = watch.last;

  let checked = "No check yet. The first audit is yours to start.";
  if (last?.error) {
    checked = `The last check failed ${ago(last.at, now)}. ${last.error}`;
  } else if (last) {
    const found = last.changed ? "Something had changed." : "Nothing had changed.";
    checked = `Checked GitHub ${ago(last.at, now)}. ${found}${last.free ? " That request used no quota." : ""}`;
  }

  let next: string | null = null;
  if (!watch.enabled) next = "Scheduled checks are switched off.";
  else if (paused) next = "No checks while paused.";
  else if (last && watch.next_at) next = `Next check ${until(watch.next_at, now)}.`;

  return (
    <div className="mt-4 border-t border-line pt-3 text-xs leading-relaxed text-faint">
      <p>{checked}</p>
      {next && <p>{next}</p>}
    </div>
  );
}

export function AgentCard({ name, role, color, status, text, mood, reserved, watch, paused = false }: Props) {
  const working = !reserved && status === "working";
  return (
    <section className={`panel p-5 ${reserved ? "opacity-70" : ""}`} aria-label={name}>
      <div className="flex items-center gap-4">
        <Face mood={reserved ? "normal" : mood} color={reserved ? "var(--fg-faint)" : color} />
        <div className="min-w-0">
          <h2 className="font-display text-xl font-semibold tracking-tight">{name}</h2>
          <p className="eyebrow mt-0.5">{role}</p>
        </div>
      </div>
      <p className="mt-4 text-sm leading-relaxed text-muted">{text}</p>
      <p className="mt-3 flex items-center gap-2 text-xs text-faint">
        {working && <span className="live-dot inline-block size-1.5 rounded-full bg-live" aria-hidden />}
        {reserved ? "Not hired yet" : (STATUS_LABEL[status] ?? status)}
      </p>
      {watch && !reserved && <WatchLines watch={watch} paused={paused} />}
    </section>
  );
}
