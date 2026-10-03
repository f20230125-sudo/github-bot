"use client";

import Link from "next/link";
import { AuditButton } from "@/components/AuditButton";
import { CiState, KindTags } from "@/components/RepoBits";
import { useStream } from "@/components/StreamProvider";
import { Notice, ScoreMeter, SeverityCounts } from "@/components/ui";
import { fetchRepos } from "@/lib/api";
import { lastFinishedRun } from "@/lib/feed";
import { plural, shortDate } from "@/lib/format";
import type { RepoCard } from "@/lib/types";
import { useApi } from "@/lib/useApi";

function Card({ repo }: { repo: RepoCard }) {
  return (
    <Link
      href={`/repos/${repo.owner}/${repo.name}`}
      className="panel flex flex-col gap-4 p-5 transition-colors hover:border-line-strong"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="truncate font-mono text-sm">{repo.name}</h2>
          <p className="mt-2 flex flex-wrap gap-1.5 empty:hidden">
            <KindTags repo={repo} />
          </p>
        </div>
        <p className="shrink-0 text-2xl font-semibold leading-none">
          {repo.score ?? <span className="text-sm font-normal text-faint">Not scored</span>}
        </p>
      </div>

      <p className="line-clamp-2 min-h-10 text-sm leading-5 text-muted">
        {repo.description ?? <span className="text-faint">No description</span>}
      </p>

      <ScoreMeter score={repo.score} />
      <SeverityCounts counts={repo.counts} />

      <p className="mt-auto flex flex-wrap gap-x-3 gap-y-1 text-xs text-faint">
        {repo.language && <span>{repo.language}</span>}
        <CiState state={repo.ci_state} />
        {repo.pushed_at && <span>Pushed {shortDate(repo.pushed_at)}</span>}
      </p>
    </Link>
  );
}

export default function ReposPage() {
  const { events } = useStream();
  const { data, error, loading } = useApi(`repos:${lastFinishedRun(events)}`, fetchRepos);

  // Worst first: that is where the work is. Repositories without a score go last.
  const repos = data ? [...data.repos].sort((a, b) => (a.score ?? 101) - (b.score ?? 101)) : [];
  const portfolio = data?.portfolio;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Repositories</p>
          <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight sm:text-4xl">Repository health.</h1>
        </div>
        <AuditButton />
      </div>

      {error && <Notice tone="error">{error}</Notice>}

      {portfolio && portfolio.score !== null && (
        <section className="panel grid items-center gap-6 p-6 sm:grid-cols-[auto_minmax(0,1fr)]" aria-label="Portfolio">
          <div>
            <p className="text-xs text-faint">Portfolio score</p>
            <p className="mt-1 text-6xl font-semibold leading-none">
              {portfolio.score}
              <span className="ml-2 text-xl font-normal text-faint">of 100</span>
            </p>
          </div>
          <div className="flex flex-col gap-3">
            <ScoreMeter score={portfolio.score} />
            <p className="text-sm text-muted">
              The average across {plural(portfolio.scored, "scored repository", "scored repositories")}.{" "}
              {portfolio.findings ? `${plural(portfolio.findings, "finding")} to fix.` : "Nothing to fix."}
            </p>
            <SeverityCounts counts={portfolio.counts} />
          </div>
        </section>
      )}

      {repos.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {repos.map((repo) => (
            <Card key={repo.full_name} repo={repo} />
          ))}
        </div>
      )}

      {!loading && !error && repos.length === 0 && (
        <div className="panel flex flex-col items-start gap-4 p-8">
          <h2 className="font-display text-xl font-semibold tracking-tight">No repositories audited yet.</h2>
          <p className="max-w-prose text-sm leading-relaxed text-muted">
            Run an audit and every repository gets a score out of 100 and a list of what to fix.
          </p>
          <AuditButton variant="primary" />
        </div>
      )}
    </div>
  );
}
