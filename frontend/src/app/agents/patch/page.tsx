"use client";

import { Ban, Check } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { AgentCard } from "@/components/AgentCard";
import { ClaudePanel } from "@/components/ClaudePanel";
import { Lessons } from "@/components/Lessons";
import { SafetyPanel } from "@/components/SafetyPanel";
import { useStream } from "@/components/StreamProvider";
import { Dot, Notice } from "@/components/ui";
import { agentMeta } from "@/lib/agents";
import { fetchSetup, fetchSheet } from "@/lib/api";
import { lastFinishedRun, lastOfType } from "@/lib/feed";
import { useApi } from "@/lib/useApi";

function List({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <h3 className="eyebrow">{title}</h3>
      <ul className="mt-2 flex flex-col gap-1.5 text-sm leading-relaxed text-muted">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export default function PatchPage() {
  const { events, status } = useStream();
  const [version, setVersion] = useState(0);
  // Re-read when Patch's status changes, a run finishes (it may have learned something), or a lesson is edited here.
  const key = `sheet:${status}:${version}:${lastOfType(events, "agent.status")}:${lastFinishedRun(events)}`;
  const { data: sheet, error } = useApi(key, fetchSheet);
  const { data: setup } = useApi(`setup:${status}`, fetchSetup);
  const meta = agentMeta("patch");

  return (
    <div className="flex flex-col gap-8">
      <div>
        <p className="eyebrow">The agent</p>
        <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight sm:text-4xl">Who Patch is.</h1>
      </div>

      {error && <Notice tone="error">{error}</Notice>}

      {sheet && (
        <div className="grid items-start gap-6 lg:grid-cols-[340px_minmax(0,1fr)]">
          <div className="flex flex-col gap-4">
            <AgentCard
              name={sheet.name}
              role={sheet.role}
              color={meta.color}
              status={sheet.status?.status ?? "idle"}
              text={sheet.status?.text ?? "Idle. Nothing to do."}
              mood={sheet.mood ?? "focused"}
              watch={sheet.watch}
              paused={sheet.paused}
            />

            <section className="panel flex flex-col gap-5 p-6" aria-label="Voice">
              <div>
                <h2 className="font-display text-xl font-semibold tracking-tight">Voice</h2>
                <p className="mt-2 text-sm leading-relaxed text-muted">{sheet.persona.summary}</p>
              </div>
              <List title="Rules" items={sheet.persona.rules} />
              <List title="Things it believes" items={sheet.persona.opinions} />
              <List title="Habits" items={sheet.persona.quirks} />
              <p className="border-t border-line pt-4 text-xs leading-relaxed text-faint">
                Every line is checked before you see it: at most {sheet.persona.max_chars} characters and{" "}
                {sheet.persona.max_sentences} sentences, no emoji, no exclamation marks, and none of these words:{" "}
                {sheet.persona.banned.join(", ")}. A line from Claude that fails, or cites a number that is not in the
                data, is replaced by a written one. The voice is only used here. Text that goes to GitHub is plain.
              </p>
              <p className="text-xs leading-relaxed text-faint">
                To change any of it, edit backend/app/agents/github/persona.toml. No code changes are needed.
              </p>
            </section>

            <section className="panel flex flex-col gap-3 p-6" aria-label="Mood">
              <h2 className="font-display text-xl font-semibold tracking-tight">Mood</h2>
              <p className="text-sm leading-relaxed text-muted">
                Worked out from the state of your repositories, never random. The first that applies wins.
              </p>
              <ul className="divide-y divide-line border-y border-line">
                {Object.entries(sheet.moods).map(([mood, cause]) => (
                  <li key={mood} className="flex items-start gap-3 py-2.5 text-sm">
                    <span className="mt-1.5 w-2 shrink-0">{mood === sheet.mood && <Dot status="good" />}</span>
                    <span>
                      <span className={`capitalize ${mood === sheet.mood ? "font-semibold" : "text-muted"}`}>
                        {mood}
                        {mood === sheet.mood && <span className="ml-2 text-xs font-normal text-faint">now</span>}
                      </span>
                      <span className="mt-0.5 block text-xs leading-relaxed text-faint">{cause}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <Lessons lessons={sheet.lessons} limit={sheet.lesson_limit} onChanged={() => setVersion((v) => v + 1)} />

            <SafetyPanel />

            <section className="panel flex flex-col gap-5 p-6" aria-label="What Patch can change on GitHub">
              <div>
                <h2 className="font-display text-xl font-semibold tracking-tight">What Patch can change on GitHub</h2>
                <p className="mt-2 text-sm leading-relaxed text-muted">
                  This is the whole list. It is fixed in the code, not in a prompt, so nothing Claude writes can add
                  to it. Each of these still waits for your approval, and none of them happens while dry-run is on.
                </p>
              </div>
              <ul className="flex flex-col gap-2 text-sm leading-relaxed">
                {sheet.writes.map((write) => (
                  <li key={write} className="flex items-start gap-3">
                    <Check size={15} className="mt-1 shrink-0 text-good" aria-hidden />
                    {write}
                  </li>
                ))}
              </ul>
              <div>
                <h3 className="eyebrow">It has no way to</h3>
                <ul className="mt-2 flex flex-col gap-2 text-sm leading-relaxed text-muted">
                  {sheet.never.map((item) => (
                    <li key={item} className="flex items-start gap-3">
                      <Ban size={15} className="mt-1 shrink-0 text-faint" aria-hidden />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
              {setup && (
                <p className="border-t border-line pt-4 text-sm leading-relaxed text-muted">
                  {setup.github.configured
                    ? `Using a token for ${setup.github.user}. What it permits is set on GitHub. `
                    : "No GitHub token: Patch reads public data only and can change nothing. "}
                  <Link href="/setup" className="underline underline-offset-4">
                    Token and permissions are on Setup.
                  </Link>
                </p>
              )}
            </section>

            <ClaudePanel />
          </div>
        </div>
      )}
    </div>
  );
}
