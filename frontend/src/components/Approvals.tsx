"use client";

import Link from "next/link";
import { fetchProposals } from "@/lib/api";
import { lastProposalEvent } from "@/lib/feed";
import { plural, shortRepo } from "@/lib/format";
import { SHOWCASE } from "@/lib/showcase";
import type { ProposalCard } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { useStream } from "./StreamProvider";

function Row({ proposal }: { proposal: ProposalCard }) {
  const what = proposal.kind === "metadata_sweep" ? "repository" : "file";
  return (
    <li>
      <Link
        href={`/proposals/${proposal.id}`}
        className="block rounded-xl border border-line p-3 transition-colors hover:border-line-strong hover:bg-surface-2"
      >
        <p className="text-sm font-medium leading-snug">{proposal.title}</p>
        {proposal.repo && <p className="mt-1 truncate font-mono text-xs text-muted">{shortRepo(proposal.repo)}</p>}
        <p className="mt-1 text-xs text-faint">
          {plural(proposal.items, what, what === "repository" ? "repositories" : undefined)}
        </p>
      </Link>
    </li>
  );
}

/** What is waiting for your decision, and what you approved as a rehearsal while dry-run was on. */
export function Approvals() {
  const { events } = useStream();
  const { data } = useApi(`approvals:${lastProposalEvent(events)}`, (signal) => fetchProposals("all", signal));

  const proposals = data?.proposals ?? [];
  const pending = proposals.filter((p) => p.status === "pending");
  const rehearsed = proposals.filter((p) => p.status === "approved" && p.result?.dry_run);

  return (
    <section className="panel p-5" aria-label="Approvals">
      <h2 className="eyebrow">{SHOWCASE ? "Suggestions" : "Waiting for you"}</h2>
      {SHOWCASE && pending.length > 0 && (
        <p className="mt-2 text-xs leading-relaxed text-faint">Open one to make the change on GitHub.</p>
      )}
      {pending.length ? (
        <ul className="mt-3 flex flex-col gap-2">
          {pending.map((proposal) => (
            <Row key={proposal.id} proposal={proposal} />
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-muted">{SHOWCASE ? "Nothing to suggest right now." : "Nothing to approve."}</p>
      )}

      {rehearsed.length > 0 && (
        <>
          <h2 className="eyebrow mt-6">Rehearsed in dry-run</h2>
          <ul className="mt-3 flex flex-col gap-2">
            {rehearsed.map((proposal) => (
              <Row key={proposal.id} proposal={proposal} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
