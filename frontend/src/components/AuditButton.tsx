"use client";

import { PenLine, RefreshCw, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { startAudit, startDraft } from "@/lib/api";
import { agentView } from "@/lib/feed";
import { useStream } from "./StreamProvider";
import { Button, InlineError } from "./ui";

type Props = {
  label: string;
  Icon: LucideIcon;
  start: () => Promise<{ queued: boolean }>;
  variant?: "primary" | "ghost";
};

/** A button that queues one of Patch's jobs. It is disabled while Patch is already working. */
function JobButton({ label, Icon, start, variant = "ghost" }: Props) {
  const { events, status, desk } = useStream();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const working = agentView(events, "patch").status === "working";
  const paused = desk?.paused ?? false;

  async function onClick() {
    setPending(true);
    setError(null);
    try {
      await start();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start it.");
    } finally {
      setPending(false);
    }
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-3">
      <Button
        variant={variant}
        onClick={onClick}
        disabled={pending || working || paused || status !== "live"}
        title={paused ? "Paused. Resume to let Patch work." : undefined}
      >
        <Icon size={14} aria-hidden />
        {label}
      </Button>
      {error && <InlineError>{error}</InlineError>}
    </span>
  );
}

/** Ask Patch to sync with GitHub and re-check whatever changed. Uses no model. */
export function AuditButton({ variant }: { variant?: "primary" | "ghost" }) {
  return <JobButton label="Run audit" Icon={RefreshCw} start={() => startAudit()} variant={variant} />;
}

/** Ask Patch to draft fixes: templates first, then Claude for the writing if your usage allows. */
export function DraftButton({ variant }: { variant?: "primary" | "ghost" }) {
  return <JobButton label="Draft fixes" Icon={PenLine} start={startDraft} variant={variant} />;
}
