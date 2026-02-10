"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { CHECKED_EVENT, checkStatus, snapshotChanged, startCheck, WORKFLOW_URL, type CheckStatus } from "@/lib/showcase";
import { Button, InlineError } from "./ui";

const POLL_MS = 4000;
/** A check takes about half a minute. Past this, something is wrong: point at the run instead. */
const GIVE_UP_MS = 180_000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

type Outcome =
  | { kind: "note"; text: string; url?: string }
  | { kind: "error"; text: string; url?: string | null };

/**
 * Starts Patch's check from the site, on the view-only build. The site's own server asks GitHub
 * to run the scheduled job now. This page then waits for it and shows what it found.
 *
 * If the server isn't connected to GitHub, the button opens the job's page on GitHub instead,
 * where the repository's owner can start it by hand.
 */
export function CheckNow() {
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  /** Wait until a run that started after `since` has finished. Returns it, or null on giving up. */
  async function finished(since: string): Promise<CheckStatus | null> {
    const startedAt = Date.parse(since);
    const deadline = Date.now() + GIVE_UP_MS;
    while (mounted.current && Date.now() < deadline) {
      await sleep(POLL_MS);
      const status = await checkStatus();
      // The new run takes a few seconds to appear. Until it does, the newest run is still the old one.
      if (status?.state === "idle" && status.at && Date.parse(status.at) > startedAt) return status;
    }
    return null;
  }

  async function onClick() {
    setBusy(true);
    setOutcome(null);
    try {
      const answer = await startCheck();
      if (!answer?.configured) {
        // No server-side connection to GitHub: point at GitHub's own page for the job instead.
        setOutcome({ kind: "note", text: "This site can't start a check by itself yet.", url: WORKFLOW_URL });
        return;
      }
      if (answer.reason === "recent") {
        setOutcome({
          kind: "note",
          text: `A check finished moments ago. Another can start in ${answer.retry_in ?? 60} seconds.`,
        });
        return;
      }
      if (!answer.since) {
        setOutcome({ kind: "error", text: "GitHub would not start the check.", url: WORKFLOW_URL });
        return;
      }

      const run = await finished(answer.since);
      if (!mounted.current) return;
      if (!run) {
        setOutcome({ kind: "error", text: "The check is taking longer than usual.", url: WORKFLOW_URL });
        return;
      }
      window.dispatchEvent(new CustomEvent(CHECKED_EVENT));
      if (run.ok === false) {
        setOutcome({ kind: "error", text: "The check failed.", url: run.url });
        return;
      }
      if (await snapshotChanged(String(run.run_id ?? Date.now()))) {
        window.location.reload(); // something changed: load it, and replay the check that found it
        return;
      }
      setOutcome({ kind: "note", text: "Checked just now. Nothing had changed." });
    } finally {
      if (mounted.current) setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-3">
      <Button onClick={onClick} disabled={busy} aria-live="polite">
        <RefreshCw size={14} className={busy ? "animate-spin" : ""} aria-hidden />
        {busy ? "Checking" : "Check now"}
      </Button>
      {busy && <span className="text-xs text-faint">Asking GitHub. This takes about half a minute.</span>}
      {outcome?.kind === "note" && (
        <span className="text-xs text-muted">
          {outcome.text}
          {outcome.url && (
            <a href={outcome.url} target="_blank" rel="noopener noreferrer" className="ml-2 text-fg underline underline-offset-4">
              Start it on GitHub
            </a>
          )}
        </span>
      )}
      {outcome?.kind === "error" && (
        <InlineError>
          {outcome.text}
          {outcome.url && (
            <a href={outcome.url} target="_blank" rel="noopener noreferrer" className="ml-2 underline underline-offset-4">
              See it on GitHub
            </a>
          )}
        </InlineError>
      )}
    </span>
  );
}
