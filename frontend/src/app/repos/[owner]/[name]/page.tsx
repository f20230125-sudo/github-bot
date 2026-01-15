"use client";

import { AlertTriangle, ArrowLeft, ArrowUpRight, Info } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { CiState, KindTags } from "@/components/RepoBits";
import { useStream } from "@/components/StreamProvider";
import { Notice, ScoreMeter, severityColor, SeverityCounts, Tag } from "@/components/ui";
import { fetchRepo } from "@/lib/api";
import { lastFinishedRun } from "@/lib/feed";
import { plural, shortDate } from "@/lib/format";
import { SEVERITIES, type FindingView } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const FIX_LABEL: Record<NonNullable<FindingView["fix"]>, string> = {
  metadata: "Patch can fix this",
  file: "Patch can fix this",
  manual: "Needs you",
};

function FindingRow({ finding }: { finding: FindingView }) {
  const Icon = finding.severity === "info" ? Info : AlertTriangle;
  return (
    <li className="flex gap-4 px-5 py-4">
      <Icon size={16} className="mt-1 shrink-0" style={{ color: severityColor(finding.severity) }} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="leading-relaxed">{finding.text}</p>
        <p className="mt-1 text-sm leading-relaxed text-muted">{finding.detail}</p>
        <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-faint">
          <span className="font-medium uppercase tracking-wide">{finding.severity}</span>
          {finding.fix && <span>{FIX_LABEL[finding.fix]}</span>}
        </p>
      </div>
      {finding.weight > 0 && (
        <p className="tabular shrink-0 font-mono text-sm text-muted" aria-label={`Costs ${finding.weight} points`}>
          −{finding.weight}
        </p>
      )}
    </li>
  );
}

export default function RepoPage() {
  const { owner, name } = useParams<{ owner: string; name: string }>();
  const { events } = useStream();
  const { data, error, loading } = useApi(`repo:${owner}/${name}:${lastFinishedRun(events)}`, (signal) =>
    fetchRepo(owner, name, signal),
  );

  const repo = data?.repo;
  // Most severe first, then by how many points each one costs.
  const findings = data
    ? [...data.findings].sort(
        (a, b) => SEVERITIES.indexOf(a.severity) - SEVERITIES.indexOf(b.severity) || b.weight - a.weight,
      )
    : [];

  return (
    <div className="flex flex-col gap-8">
      <Link href="/repos" className="inline-flex items-center gap-2 self-start text-sm text-muted hover:text-fg">
        <ArrowLeft size={15} aria-hidden />
        Repositories
      </Link>

      {error && <Notice tone="error">{error}</Notice>}
      {loading && !data && <p className="text-sm text-muted">Loading.</p>}

      {repo && data && (
        <>
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <h1 className="break-words font-display text-3xl font-semibold tracking-tight sm:text-4xl">{repo.name}</h1>
              <p className="mt-3 max-w-prose leading-relaxed text-muted">
                {repo.description ?? <span className="text-faint">No description</span>}
              </p>
              <p className="mt-4 flex flex-wrap gap-1.5 empty:hidden">
                <KindTags repo={repo} />
                {repo.topics.map((topic) => (
                  <Tag key={topic}>{topic}</Tag>
                ))}
              </p>
            </div>
            {repo.html_url && (
              <a
                href={repo.html_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-full border border-line-strong px-4 py-2 text-sm font-medium hover:bg-surface-2"
              >
                Open on GitHub
                <ArrowUpRight size={15} aria-hidden />
              </a>
            )}
          </div>

          <section className="panel grid items-center gap-6 p-6 sm:grid-cols-[auto_minmax(0,1fr)]" aria-label="Score">
            <div>
              <p className="text-xs text-faint">Health score</p>
              <p className="mt-1 text-6xl font-semibold leading-none">
                {repo.score ?? <span className="text-2xl font-normal text-faint">Not scored</span>}
                {repo.score !== null && <span className="ml-2 text-xl font-normal text-faint">of 100</span>}
              </p>
            </div>
            <div className="flex flex-col gap-3">
              <ScoreMeter score={repo.score} />
              <SeverityCounts counts={repo.counts} />
              <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-faint">
                {repo.language && <span>{repo.language}</span>}
                <span>{repo.license ? `License: ${repo.license}` : "No license detected"}</span>
                <CiState state={repo.ci_state} />
                <span>{plural(data.files, "file")}</span>
                {data.readme.path && <span>README: {data.readme.chars.toLocaleString()} characters</span>}
                {repo.pushed_at && <span>Pushed {shortDate(repo.pushed_at)}</span>}
              </p>
            </div>
          </section>

          <section aria-label="Findings">
            <h2 className="eyebrow mb-3">Findings</h2>
            {findings.length ? (
              <ul className="panel divide-y divide-line overflow-hidden">
                {findings.map((finding) => (
                  <FindingRow key={`${finding.check}-${finding.detail}`} finding={finding} />
                ))}
              </ul>
            ) : (
              <p className="panel p-5 text-sm text-muted">Nothing to fix.</p>
            )}
          </section>

          {data.history.length > 1 && (
            <section aria-label="Score history">
              <h2 className="eyebrow mb-3">Score history</h2>
              <table className="panel w-full overflow-hidden text-left text-sm">
                <thead>
                  <tr className="border-b border-line text-xs text-faint">
                    <th className="px-5 py-3 font-normal">Date</th>
                    <th className="px-5 py-3 text-right font-normal">Score</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {[...data.history].reverse().map((entry) => (
                    <tr key={entry.ts}>
                      <td className="px-5 py-3 text-muted">{shortDate(entry.ts)}</td>
                      <td className="tabular px-5 py-3 text-right font-mono">{entry.score}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </>
      )}
    </div>
  );
}
