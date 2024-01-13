"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ProposalEditor } from "@/components/ProposalEditor";
import { ProposalOutcome } from "@/components/ProposalOutcome";
import { useStream } from "@/components/StreamProvider";
import { Notice, Tag } from "@/components/ui";
import { fetchPolicy, fetchProposal } from "@/lib/api";
import { lastProposalEvent } from "@/lib/feed";
import { shortDate, shortRepo } from "@/lib/format";
import { useApi } from "@/lib/useApi";

const KIND_LABEL = { metadata_sweep: "Descriptions and topics", pull_request: "Pull request" } as const;

export default function ProposalPage() {
  const { id } = useParams<{ id: string }>();
  const { events } = useStream();
  const version = lastProposalEvent(events);
  const { data, error, loading } = useApi(`proposal:${id}:${version}`, (signal) => fetchProposal(Number(id), signal));
  const { data: policy } = useApi(`policy:${version}`, fetchPolicy);
  const dryRun = policy?.dry_run ?? true;

  return (
    <div className="flex max-w-4xl flex-col gap-8">
      <Link href="/" className="inline-flex items-center gap-2 self-start text-sm text-muted hover:text-fg">
        <ArrowLeft size={15} aria-hidden />
        Floor
      </Link>

      {error && <Notice tone="error">{error}</Notice>}
      {loading && !data && <p className="text-sm text-muted">Loading.</p>}

      {data && (
        <>
          <div>
            <p className="flex flex-wrap items-center gap-2">
              <Tag>{KIND_LABEL[data.kind]}</Tag>
              {data.repo && (
                <Link href={`/repos/${data.repo}`} className="font-mono text-xs text-muted underline-offset-4 hover:underline">
                  {shortRepo(data.repo)}
                </Link>
              )}
              <span className="text-xs text-faint">Drafted {shortDate(data.created_at)}</span>
            </p>
            <h1 className="mt-3 font-display text-3xl font-semibold tracking-tight sm:text-4xl">{data.title}</h1>
            <p className="mt-3 max-w-prose leading-relaxed text-muted">{data.summary}</p>
          </div>

          {data.status === "pending" ? (
            // Keyed on the version, so a redraft replaces your unsaved edits instead of mixing with them.
            <ProposalEditor key={`${data.id}:${data.updated_at}`} proposal={data} dryRun={dryRun} />
          ) : (
            <ProposalOutcome proposal={data} dryRun={dryRun} />
          )}
        </>
      )}
    </div>
  );
}
