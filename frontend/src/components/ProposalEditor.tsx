"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { approveProposal, rejectProposal, type ProposalEdits } from "@/lib/api";
import { shortRepo } from "@/lib/format";
import type { FileItem, ProposalDetail, SweepItem } from "@/lib/types";
import { Diff } from "./Diff";
import { Button, InlineError, Tag } from "./ui";

const FIELD =
  "w-full rounded-xl border border-line-strong bg-sunken px-3 py-2 text-sm outline-offset-2 placeholder:text-faint disabled:opacity-50";

type SweepRow = { item: SweepItem; enabled: boolean; description: string | null; topics: string | null };
type FileRow = { file: FileItem; enabled: boolean; content: string; editing: boolean };

function parseTopics(text: string): string[] {
  return text
    .split(",")
    .map((topic) => topic.trim())
    .filter(Boolean);
}

function SweepRows({ rows, onChange }: { rows: SweepRow[]; onChange: (rows: SweepRow[]) => void }) {
  const update = (index: number, change: Partial<SweepRow>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...change } : row)));

  return (
    <ul className="panel divide-y divide-line overflow-hidden">
      {rows.map((row, index) => {
        const name = shortRepo(row.item.repo);
        return (
          <li key={row.item.repo} className="flex flex-col gap-4 p-5">
            <label className="flex items-center gap-3">
              <input
                type="checkbox"
                checked={row.enabled}
                onChange={(e) => update(index, { enabled: e.target.checked })}
                className="size-4 accent-[var(--fg)]"
              />
              <span className="font-mono text-sm">{name}</span>
            </label>

            {row.description !== null && (
              <label className="flex flex-col gap-2 text-xs text-faint">
                Description
                <span className="text-muted">
                  Now: {row.item.current_description ?? <span className="text-faint">none</span>}
                </span>
                <textarea
                  value={row.description}
                  onChange={(e) => update(index, { description: e.target.value })}
                  disabled={!row.enabled}
                  rows={2}
                  maxLength={350}
                  className={`${FIELD} text-fg`}
                />
              </label>
            )}

            {row.topics !== null && (
              <label className="flex flex-col gap-2 text-xs text-faint">
                Topics, separated by commas
                <span className="text-muted">
                  Now: {row.item.current_topics.length ? row.item.current_topics.join(", ") : <span className="text-faint">none</span>}
                </span>
                <input
                  type="text"
                  value={row.topics}
                  onChange={(e) => update(index, { topics: e.target.value })}
                  disabled={!row.enabled}
                  spellCheck={false}
                  className={`${FIELD} font-mono text-xs text-fg`}
                />
              </label>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function FileRows({ rows, onChange }: { rows: FileRow[]; onChange: (rows: FileRow[]) => void }) {
  const update = (index: number, change: Partial<FileRow>) =>
    onChange(rows.map((row, i) => (i === index ? { ...row, ...change } : row)));

  return (
    <ul className="flex flex-col gap-4">
      {rows.map((row, index) => {
        const { file } = row;
        const edited = row.content !== file.content;
        return (
          <li key={file.path} className="panel overflow-hidden">
            <div className="flex flex-col gap-3 p-5">
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-3">
                  <input
                    type="checkbox"
                    checked={row.enabled}
                    onChange={(e) => update(index, { enabled: e.target.checked })}
                    className="size-4 accent-[var(--fg)]"
                  />
                  <span className="font-mono text-sm">{file.path}</span>
                </label>
                <Tag>{file.source === "template" ? "Template" : "Written by Claude"}</Tag>
                <Tag>{file.is_new ? "New file" : "Changed"}</Tag>
                <Button className="ml-auto" onClick={() => update(index, { editing: !row.editing })} disabled={!row.enabled}>
                  {row.editing ? "Show changes" : "Edit"}
                </Button>
              </div>
              <p className="text-sm leading-relaxed text-muted">{file.reason}</p>
              {file.note && (
                <p className="flex items-start gap-2 text-sm leading-relaxed">
                  <AlertTriangle size={15} className="mt-0.5 shrink-0 text-warning" aria-hidden />
                  {file.note}
                </p>
              )}
            </div>

            <div className={`border-t border-line ${row.enabled ? "" : "opacity-50"}`}>
              {row.editing ? (
                <textarea
                  value={row.content}
                  onChange={(e) => update(index, { content: e.target.value })}
                  rows={18}
                  spellCheck={false}
                  aria-label={`Contents of ${file.path}`}
                  className="block w-full resize-y bg-sunken p-4 font-mono text-xs leading-relaxed outline-none"
                />
              ) : edited ? (
                <p className="px-5 py-3 text-sm text-muted">You edited this file. Your version is what gets approved.</p>
              ) : (
                <Diff lines={file.diff} />
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** A pending proposal: switch items off, edit them, then approve or reject. */
export function ProposalEditor({ proposal, dryRun }: { proposal: ProposalDetail; dryRun: boolean }) {
  const isSweep = proposal.kind === "metadata_sweep";
  const [sweep, setSweep] = useState<SweepRow[]>(() =>
    (proposal.payload.items ?? []).map((item) => ({
      item,
      enabled: item.enabled,
      description: item.description,
      topics: item.topics ? item.topics.join(", ") : null,
    })),
  );
  const [files, setFiles] = useState<FileRow[]>(() =>
    (proposal.payload.files ?? []).map((file) => ({ file, enabled: file.enabled, content: file.content, editing: false })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");

  const anyEnabled = isSweep ? sweep.some((r) => r.enabled) : files.some((r) => r.enabled);

  function edits(): ProposalEdits {
    if (isSweep) {
      return {
        items: sweep.map((row) => ({
          repo: row.item.repo,
          enabled: row.enabled,
          ...(row.description !== null ? { description: row.description } : {}),
          ...(row.topics !== null ? { topics: parseTopics(row.topics) } : {}),
        })),
      };
    }
    return {
      files: files.map((row) => ({
        path: row.file.path,
        enabled: row.enabled,
        ...(row.content !== row.file.content ? { content: row.content } : {}),
      })),
    };
  }

  async function decide(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
      setBusy(false);
    }
    // On success the page reloads the proposal from the live feed, and this editor goes away.
  }

  const consequence = dryRun
    ? "Dry-run is on. Approving shows what would happen. Nothing is written to GitHub."
    : isSweep
      ? "Approving changes these descriptions and topics on GitHub."
      : "Approving opens a pull request on GitHub. Nothing reaches your default branch until you merge it.";

  return (
    <div className="flex flex-col gap-6">
      {isSweep ? <SweepRows rows={sweep} onChange={setSweep} /> : <FileRows rows={files} onChange={setFiles} />}

      {(proposal.payload.notes ?? []).length > 0 && (
        <ul className="flex list-disc flex-col gap-1 pl-5 text-sm text-muted">
          {proposal.payload.notes?.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}

      <section className="panel flex flex-col gap-4 p-5" aria-label="Decide">
        <p className="text-sm leading-relaxed text-muted">{consequence}</p>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            disabled={busy || !anyEnabled}
            onClick={() => decide(() => approveProposal(proposal.id, edits()))}
          >
            {busy ? "Working" : dryRun ? "Approve as a rehearsal" : "Approve and apply"}
          </Button>
          <Button disabled={busy} onClick={() => setRejecting((value) => !value)}>
            Reject
          </Button>
          {!anyEnabled && <span className="text-sm text-muted">Everything is switched off.</span>}
          {error && <InlineError>{error}</InlineError>}
        </div>

        {rejecting && (
          <form
            className="flex flex-col gap-3 sm:flex-row sm:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              void decide(() => rejectProposal(proposal.id, reason));
            }}
          >
            <label className="flex min-w-0 flex-1 flex-col gap-2 text-xs text-faint">
              Why? Optional, but Patch learns from it.
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                maxLength={300}
                placeholder="Too generic"
                className={`${FIELD} text-fg`}
              />
            </label>
            <Button type="submit" disabled={busy}>
              Confirm reject
            </Button>
          </form>
        )}
      </section>
    </div>
  );
}
