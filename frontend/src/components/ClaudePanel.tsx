"use client";

import { useState } from "react";
import { fetchClaude, testClaude } from "@/lib/api";
import { lastFinishedRun } from "@/lib/feed";
import { resetTime, statusColor } from "@/lib/format";
import type { ClaudeStatus, ClaudeTest, UsageWindow } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { useStream } from "./StreamProvider";
import { Button, Dot, InlineError } from "./ui";

/** Plan usage against the stop. The tick marks the percentage at which Patch stops calling Claude. */
export function UsageMeter({ label, window, limit }: { label: string; window: UsageWindow; limit: number }) {
  const percent = window.percent;
  if (percent === null) {
    return (
      <div className="flex items-baseline justify-between gap-4 py-2 text-sm">
        <span className="text-muted">{label}</span>
        <span className="text-faint">Not reported</span>
      </div>
    );
  }
  const color = statusColor(percent >= limit ? "critical" : "good");
  return (
    <div className="py-2">
      <div className="flex items-baseline justify-between gap-4 text-sm">
        <span className="text-muted">{label}</span>
        <span>
          <span className="font-semibold">{Math.round(percent)}% used</span>
          <span className="ml-2 text-xs text-faint">stops at {limit}%</span>
        </span>
      </div>
      <div
        className="relative mt-2 h-1.5 w-full rounded-r-[3px]"
        style={{ background: `color-mix(in oklab, ${color} 22%, var(--surface))` }}
        role="meter"
        aria-label={`${label} usage`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
      >
        <div className="h-full rounded-r-[3px]" style={{ width: `${Math.min(percent, 100)}%`, background: color }} />
        <div className="absolute -top-1 h-3.5 w-px bg-fg" style={{ left: `${limit}%` }} aria-hidden />
      </div>
      {window.minutes_old !== null && (
        <p className="mt-1 text-xs text-faint">
          Read {window.minutes_old === 0 ? "just now" : `${window.minutes_old} min ago`} from the {window.source}.
          {window.resets_at !== null && ` Resets ${resetTime(window.resets_at)}.`}
        </p>
      )}
    </div>
  );
}

function TestReport({ report }: { report: ClaudeTest }) {
  const { usage, structured } = report;
  return (
    <div className="flex flex-col gap-3 border-t border-line pt-4 text-sm leading-relaxed">
      {usage && (
        <p className="text-muted">
          {usage.ok
            ? usage.answered_locally
              ? "Claude Code answered the usage check by itself. That costs nothing."
              : `The usage check went to the model: ${usage.tokens} tokens.`
            : `The usage check failed: ${usage.error}`}
        </p>
      )}
      {structured && (
        <p className="text-muted">
          {structured.skipped
            ? `The test call was skipped. ${structured.error}`
            : structured.ok
              ? `A stripped-down call works: ${structured.tokens} tokens on ${structured.model}, structured ${
                  structured.via === "schema" ? "by the CLI's schema check" : "by reading JSON from the reply"
                }.`
              : `The test call failed: ${structured.error}`}
        </p>
      )}
      {usage?.text && (
        <details>
          <summary className="cursor-pointer text-xs text-faint">What Claude Code reported</summary>
          <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-xl bg-sunken p-3 font-mono text-xs text-muted">
            {usage.text}
            {usage.reports?.length ? `\n\n${JSON.stringify(usage.reports, null, 2)}` : ""}
          </pre>
        </details>
      )}
    </div>
  );
}

export function ClaudePanel() {
  const { events } = useStream();
  const [version, setVersion] = useState(0);
  const { data, error } = useApi(`claude:${version}:${lastFinishedRun(events)}`, fetchClaude);
  const [report, setReport] = useState<ClaudeTest | null>(null);
  const [busy, setBusy] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);

  async function onTest() {
    setBusy(true);
    setTestError(null);
    try {
      setReport(await testClaude());
      setVersion((v) => v + 1);
    } catch (e) {
      setTestError(e instanceof Error ? e.message : "The test didn't run.");
    } finally {
      setBusy(false);
    }
  }

  const status: ClaudeStatus | null = report ?? data;

  return (
    <section className="panel flex flex-col gap-5 p-6" aria-label="Claude">
      <div>
        <h2 className="font-display text-xl font-semibold tracking-tight">Claude</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          Patch calls the Claude Code program signed in on this machine, so it uses your plan and never an API key.
          It stops calling Claude once your usage reaches the stop below.
        </p>
      </div>

      {error && <InlineError>{error}</InlineError>}

      {status && (
        <>
          <div className="flex items-start gap-3">
            <Dot status={status.auth.ok ? "good" : "critical"} className="mt-2" />
            <div>
              <p className="font-medium">
                {status.auth.ok ? `Signed in with your Claude ${status.auth.plan ?? ""} plan` : "Claude can't be used"}
              </p>
              {status.auth.problem && <p className="mt-1 text-sm text-muted">{status.auth.problem}</p>}
            </div>
          </div>

          {status.auth.ok && (
            <>
              <div className="divide-y divide-line border-y border-line">
                <UsageMeter label="5-hour limit" window={status.guard.windows.session} limit={status.guard.limits.session} />
                <UsageMeter label="Weekly limit" window={status.guard.windows.weekly} limit={status.guard.limits.weekly} />
              </div>
              <div className="flex items-start gap-3">
                <Dot status={status.guard.allowed ? "good" : "warning"} className="mt-2" />
                <div>
                  <p className="font-medium">{status.guard.allowed ? "Patch may call Claude" : "Patch is not calling Claude"}</p>
                  <p className="mt-1 text-sm text-muted">
                    {status.guard.reason}
                    {status.guard.code === "no_reading" && " Run the test below to take a first reading."}
                  </p>
                </div>
              </div>
            </>
          )}
        </>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={onTest} disabled={busy || status?.auth.installed === false}>
          {busy ? "Testing" : "Test Claude connection"}
        </Button>
        <span className="text-xs text-faint">Makes at most two tiny calls on your plan.</span>
        {testError && <InlineError>{testError}</InlineError>}
      </div>

      {report && <TestReport report={report} />}

      {data?.models && (
        <p className="text-xs leading-relaxed text-faint">
          Writing uses {data.models.writing}, small jobs use {data.models.small}. Change them with DESK_CLAUDE_MODEL_WRITE
          and DESK_CLAUDE_MODEL_SMALL in backend/.env.
        </p>
      )}
    </section>
  );
}
