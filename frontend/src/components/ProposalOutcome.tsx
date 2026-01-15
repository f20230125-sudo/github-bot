"use client";

import { ArrowUpRight, Check, Copy, XCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { applyProposal } from "@/lib/api";
import { shortDate, shortRepo } from "@/lib/format";
import type { ActionResult, ProposalDetail } from "@/lib/types";
import { Button, InlineError, Tag } from "./ui";

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      onClick={async () => {
        await navigator.clipboard.writeText(value);
        setCopied(true);
      }}
    >
      <Copy size={13} aria-hidden />
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function ActionRow({ action }: { action: ActionResult }) {
  const Icon = action.ok ? Check : XCircle;
  const value = Array.isArray(action.value) ? action.value.join(", ") : action.value;
  return (
    <li className="flex flex-col gap-2 px-5 py-3">
      <p className="flex items-start gap-3 text-sm leading-relaxed">
        <Icon size={15} className={`mt-1 shrink-0 ${action.ok ? "text-good" : "text-critical"}`} aria-hidden />
        <span className="min-w-0">
          <span className="mr-2 font-mono text-xs">{shortRepo(action.repo)}</span>
          <span className="text-muted">{action.text}</span>
        </span>
        {action.url && (
          <a
            href={action.url}
            target="_blank"
            rel="noopener noreferrer"
            className="ml-auto inline-flex shrink-0 items-center gap-1 text-sm underline underline-offset-4"
          >
            Open
            <ArrowUpRight size={13} aria-hidden />
          </a>
        )}
      </p>
      {/* Refused for lack of a permission: here is the text, to paste in yourself. */}
      {!action.ok && action.needed && value && (
        <div className="ml-7 flex flex-wrap items-center gap-3">
          <code className="min-w-0 break-words rounded-lg bg-sunken px-3 py-2 font-mono text-xs">{value}</code>
          <CopyButton value={value} />
          <a
            href={`https://github.com/${action.repo}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm underline underline-offset-4"
          >
            Open the repository
            <ArrowUpRight size={13} aria-hidden />
          </a>
        </div>
      )}
    </li>
  );
}

const STATUS_COPY: Record<string, string> = {
  approved: "Approved",
  rejected: "Rejected",
  applied: "Applied",
  failed: "Not applied",
  superseded: "Replaced by a newer draft",
};

/** A proposal you have already decided on: what was in it, and what happened. */
export function ProposalOutcome({ proposal, dryRun }: { proposal: ProposalDetail; dryRun: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { decision, result } = proposal;
  const rehearsed = proposal.status === "approved" && result?.dry_run === true;

  async function onApply() {
    setBusy(true);
    setError(null);
    try {
      await applyProposal(proposal.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start it.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="panel flex flex-col gap-3 p-5" aria-label="Decision">
        <p className="font-medium">
          {STATUS_COPY[proposal.status] ?? proposal.status}
          {decision?.at && <span className="ml-2 text-sm font-normal text-faint">{shortDate(decision.at)}</span>}
        </p>
        {decision?.reason && <p className="text-sm text-muted">Your reason: {decision.reason}</p>}
        {(decision?.edits?.length ?? 0) > 0 && <p className="text-sm text-muted">You edited it before approving.</p>}
        {result?.stale && (
          <p className="text-sm text-muted">The repository changed after this was drafted, so Patch drafted it again.</p>
        )}

        {rehearsed && (
          <div className="flex flex-col gap-3 border-t border-line pt-4">
            <p className="text-sm leading-relaxed text-muted">
              This was a rehearsal. Nothing was written to GitHub.{" "}
              {dryRun ? (
                <>
                  To apply it for real, switch dry-run off in <Link href="/setup" className="underline underline-offset-4">Setup</Link>.
                </>
              ) : (
                "Dry-run is off now, so you can apply it for real."
              )}
            </p>
            {!dryRun && (
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="primary" onClick={onApply} disabled={busy}>
                  {busy ? "Starting" : "Apply for real"}
                </Button>
                {error && <InlineError>{error}</InlineError>}
              </div>
            )}
          </div>
        )}
      </section>

      {result && result.actions.length > 0 && (
        <section aria-label="What happened">
          <h2 className="eyebrow mb-3">{result.dry_run ? "What would happen" : "What happened"}</h2>
          <ul className="panel divide-y divide-line overflow-hidden">
            {result.actions.map((action, index) => (
              <ActionRow key={index} action={action} />
            ))}
          </ul>
        </section>
      )}

      <section aria-label="Contents">
        <h2 className="eyebrow mb-3">What was in it</h2>
        <ul className="panel divide-y divide-line overflow-hidden">
          {(proposal.payload.items ?? []).map((item) => (
            <li key={item.repo} className={`flex flex-col gap-2 px-5 py-4 ${item.enabled ? "" : "opacity-50"}`}>
              <p className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm">{shortRepo(item.repo)}</span>
                {!item.enabled && <Tag>Left out</Tag>}
              </p>
              {item.description && <p className="text-sm text-muted">{item.description}</p>}
              {item.topics && <p className="font-mono text-xs text-faint">{item.topics.join(", ")}</p>}
            </li>
          ))}
          {(proposal.payload.files ?? []).map((file) => (
            <li key={file.path} className={`flex flex-col gap-2 px-5 py-4 ${file.enabled ? "" : "opacity-50"}`}>
              <p className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm">{file.path}</span>
                <Tag>{file.source === "template" ? "Template" : "Written by Claude"}</Tag>
                {!file.enabled && <Tag>Left out</Tag>}
              </p>
              <p className="text-sm text-muted">{file.reason}</p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
