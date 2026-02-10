"use client";

import { Play, RotateCcw, SkipForward } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { AgentCard } from "@/components/AgentCard";
import { Approvals } from "@/components/Approvals";
import { AuditButton, DraftButton } from "@/components/AuditButton";
import { Chat } from "@/components/Chat";
import { CheckNow } from "@/components/CheckNow";
import { Feed } from "@/components/Feed";
import { useStream } from "@/components/StreamProvider";
import { Button, InlineError, Notice, StatRow, Tag } from "@/components/ui";
import { agentMeta } from "@/lib/agents";
import { API_URL, ApiError, fetchHandoffs, fetchHealth, fetchMetrics, playDemo } from "@/lib/api";
import { agentView, buildFeed, lastFinishedRun, lastMessage } from "@/lib/feed";
import { shortDate, shortRepo } from "@/lib/format";
import type { Handoff } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const TOPIC_LABEL: Record<string, string> = {
  new_repo: "New repository",
  release: "Release",
  demo_link: "Live link",
  stars: "Stars",
  ready: "Presentable",
};

/** What Patch has left for the LinkedIn agent. Nobody answers yet, and nothing pretends to. */
function PitchTray({ handoffs }: { handoffs: Handoff[] }) {
  return (
    <section className="panel p-5" aria-label="Waiting for Pitch">
      <h2 className="eyebrow">Waiting for Pitch</h2>
      {handoffs.length ? (
        <ul className="mt-3 flex flex-col gap-3">
          {handoffs.map((handoff) => (
            <li key={handoff.id} className="text-sm leading-relaxed">
              <p className="flex flex-wrap items-center gap-2">
                <Tag>{TOPIC_LABEL[handoff.topic] ?? handoff.topic}</Tag>
                {handoff.repo && <span className="font-mono text-xs">{shortRepo(handoff.repo)}</span>}
                <time className="text-xs text-faint" dateTime={handoff.ts}>
                  {shortDate(handoff.ts)}
                </time>
              </p>
              <p className="mt-1 text-muted">{handoff.text}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm leading-relaxed text-muted">
          Nothing yet. When a repository gets a release, a live link or a star milestone, Patch leaves a note here.
        </p>
      )}
    </section>
  );
}

export default function FloorPage() {
  const { events, status, desk, replay } = useStream();
  const [demoBusy, setDemoBusy] = useState(false);
  const [demoError, setDemoError] = useState<string | null>(null);

  // Re-read the API's state whenever the connection comes back or a run finishes.
  const { data: health } = useApi(`health:${status}:${lastFinishedRun(events)}`, fetchHealth);
  const { data: tray } = useApi(`handoffs:${status}:${lastMessage(events, "handoff")}`, fetchHandoffs);

  const feed = useMemo(() => buildFeed(events), [events]);
  const patch = agentView(events, "patch");
  const showingDemo = events.some((e) => e.payload.demo === true);
  const paused = desk?.paused ?? false;
  const watch = desk?.agents.find((agent) => agent.id === "patch")?.watch;
  // The day's totals move when a run finishes and when a quiet check goes by.
  const { data: metrics } = useApi(
    `today:${status}:${lastFinishedRun(events)}:${watch?.last?.at ?? ""}`,
    (signal) => fetchMetrics(1, signal),
  );
  const today = metrics?.today;

  async function onPlayDemo() {
    setDemoBusy(true);
    setDemoError(null);
    try {
      await playDemo(1);
    } catch (error) {
      setDemoError(
        error instanceof ApiError && error.status === 409
          ? "The recording is already playing."
          : "Couldn't start the recording. Check that the API is running.",
      );
    } finally {
      setDemoBusy(false);
    }
  }

  const patchMeta = agentMeta("patch");
  const pitchMeta = agentMeta("pitch");

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">The floor</p>
          <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            Every step, as it happens.
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          {health && (
            <>
              <span className="rounded-full border border-line px-3 py-1.5">
                {health.dry_run ? "Dry-run on: nothing is written to GitHub" : "Dry-run off"}
              </span>
              <Link href="/setup" className="rounded-full border border-line px-3 py-1.5 hover:border-line-strong hover:text-fg">
                {health.github_configured ? `GitHub: ${health.github_user}` : "GitHub: public data only"}
              </Link>
            </>
          )}
          <AuditButton />
          <DraftButton />
          {replay && (
            // The view-only copy plays a recording of the latest check in place of the live feed.
            <>
              <Button onClick={replay.playing ? replay.finish : replay.restart}>
                {replay.playing ? <SkipForward size={14} aria-hidden /> : <RotateCcw size={14} aria-hidden />}
                {replay.playing ? "Skip to the end" : "Replay the last check"}
              </Button>
              <CheckNow />
            </>
          )}
        </div>
      </div>

      {/* Three columns on a wide screen: who is working, what they are doing, what needs you. */}
      <div className="grid gap-6 lg:grid-cols-[300px_minmax(0,1fr)] xl:grid-cols-[300px_minmax(0,1fr)_300px]">
        <aside className="flex flex-col gap-4">
          <AgentCard
            name={patchMeta.name}
            role={patchMeta.role}
            color={patchMeta.color}
            status={patch.status}
            text={patch.text}
            mood={patch.mood}
            watch={watch}
            paused={paused}
          />
          <AgentCard
            name={pitchMeta.name}
            role={pitchMeta.role}
            color={pitchMeta.color}
            status="idle"
            text="Seat reserved. The LinkedIn agent joins the desk in the next round."
            mood="focused"
            reserved
          />

          <section className="panel p-5" aria-label="Today">
            <div className="flex items-baseline justify-between gap-3">
              <h2 className="eyebrow">Today</h2>
              <Link href="/metrics" className="text-xs text-muted underline underline-offset-4 hover:text-fg">
                All metrics
              </Link>
            </div>
            {today ? (
              <dl className="mt-2 divide-y divide-line">
                <StatRow
                  label="GitHub requests"
                  value={today.github_requests}
                  note={today.github_not_modified ? `${today.github_not_modified} unchanged` : undefined}
                />
                <StatRow label="Model calls" value={today.claude_calls} />
                <StatRow label="Calls avoided" value={today.calls_avoided} />
                <StatRow
                  label="Checks"
                  value={today.checks}
                  note={today.idle_checks ? `${today.idle_checks} found nothing` : undefined}
                />
              </dl>
            ) : (
              <p className="mt-3 text-sm text-muted">Nothing yet.</p>
            )}
            <p className="mt-3 text-xs leading-relaxed text-faint">
              A call is avoided each time rules do a job that would otherwise need Claude.
            </p>
          </section>

          <PitchTray handoffs={tray?.handoffs ?? []} />
        </aside>

        <section aria-label="Live feed" className="flex min-w-0 flex-col gap-4">
          {paused && (
            <Notice>
              Paused. Patch runs nothing, checks nothing and calls nothing until you press Resume. It still answers
              from stored data.
            </Notice>
          )}
          {showingDemo && <Notice>Some of this is a recording. Nothing marked Recording was done to your GitHub.</Notice>}
          {status === "offline" && <Notice tone="error">The API isn&apos;t reachable at {API_URL}. Start it with dev.ps1.</Notice>}

          <Chat />

          {feed.length ? (
            <Feed items={feed} />
          ) : replay ? (
            <p className="panel p-8 text-sm text-muted">The recording is starting.</p>
          ) : (
            <div className="panel flex flex-col items-start gap-4 p-8">
              <h2 className="font-display text-xl font-semibold tracking-tight">Nothing has happened yet.</h2>
              <p className="max-w-prose text-sm leading-relaxed text-muted">
                Run an audit and Patch will check every repository and show each step here. Without a GitHub token
                it reads your public repositories only. To see the floor without touching GitHub, play a recorded
                session.
              </p>
              <AuditButton variant="primary" />
            </div>
          )}

          {!replay && (
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={onPlayDemo} disabled={demoBusy || status !== "live"}>
                <Play size={14} aria-hidden />
                {demoBusy ? "Starting" : "Play the recorded demo"}
              </Button>
              {demoError && <InlineError>{demoError}</InlineError>}
            </div>
          )}
        </section>

        {/* Below the feed until the screen is wide enough for a third column. */}
        <aside className="lg:col-start-2 xl:col-start-3">
          <Approvals />
        </aside>
      </div>
    </div>
  );
}
