"use client";

import { Ban, Check } from "lucide-react";
import { AgentCard } from "@/components/AgentCard";
import { NoteCard } from "@/components/Notes";
import { useStream } from "@/components/StreamProvider";
import { Notice } from "@/components/ui";
import { agentMeta } from "@/lib/agents";
import { fetchNotes, fetchPitch } from "@/lib/api";
import { agentView, lastFinishedRun, lastOfType } from "@/lib/feed";
import { plural } from "@/lib/format";
import { SHOWCASE } from "@/lib/showcase";
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

export default function PitchPage() {
  const { events, status } = useStream();
  // Re-read when an agent's status changes or a run finishes: either can change what a note is worth.
  const key = `${status}:${lastOfType(events, "agent.status")}:${lastFinishedRun(events)}`;
  const { data: sheet, error } = useApi(`pitch:${key}`, fetchPitch);
  const { data: tray } = useApi(`notes:${key}`, fetchNotes);
  const meta = agentMeta("pitch");

  // What can be posted comes first. Within each group the newest note stays on top.
  const all = tray?.handoffs ?? [];
  const notes = [...all.filter((note) => note.brief?.ready), ...all.filter((note) => !note.brief?.ready)];
  const ready = all.filter((note) => note.brief?.ready).length;
  const view = agentView(events, "pitch", sheet?.status ?? { status: "idle", text: "Nothing worth a post yet.", mood: "normal" });

  return (
    <div className="flex flex-col gap-8">
      <div>
        <p className="eyebrow">The agent</p>
        <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight sm:text-4xl">What Pitch can post about.</h1>
      </div>

      {error && (
        <Notice tone={SHOWCASE ? "info" : "error"}>
          {SHOWCASE ? "Pitch has not joined this copy yet. It takes its seat at the next check." : error}
        </Notice>
      )}

      {sheet && (
        <div className="grid items-start gap-6 lg:grid-cols-[340px_minmax(0,1fr)]">
          <div className="flex flex-col gap-4">
            <AgentCard
              name={sheet.name}
              role={sheet.role}
              color={meta.color}
              status={view.status}
              text={view.text}
              mood={sheet.mood ?? view.mood}
              paused={sheet.paused}
            />

            <section className="panel flex flex-col gap-5 p-6" aria-label="What Pitch does">
              <div>
                <h2 className="font-display text-xl font-semibold tracking-tight">What Pitch does</h2>
                <p className="mt-2 text-sm leading-relaxed text-muted">
                  So far it reads. It uses rules only: no model, and no request to GitHub or anywhere else. Writing
                  the post comes next, and pressing Post will always be yours.
                </p>
              </div>
              <ul className="flex flex-col gap-2 text-sm leading-relaxed">
                {sheet.does.map((item) => (
                  <li key={item} className="flex items-start gap-3">
                    <Check size={15} className="mt-1 shrink-0 text-good" aria-hidden />
                    {item}
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
            </section>

            <section className="panel flex flex-col gap-5 p-6" aria-label="Voice">
              <div>
                <h2 className="font-display text-xl font-semibold tracking-tight">Voice</h2>
                <p className="mt-2 text-sm leading-relaxed text-muted">{sheet.persona.summary}</p>
              </div>
              <List title="Rules" items={sheet.persona.rules} />
              <List title="Things it believes" items={sheet.persona.opinions} />
              <List title="Habits" items={sheet.persona.quirks} />
              <p className="border-t border-line pt-4 text-xs leading-relaxed text-faint">
                This is how Pitch talks on this site. A post goes out under your name, so it will be written as
                you. To change the voice, edit backend/app/agents/linkedin/persona.toml.
              </p>
            </section>
          </div>

          <section className="flex min-w-0 flex-col gap-4" aria-label="Notes from Patch">
            <div className="panel p-6">
              <h2 className="font-display text-xl font-semibold tracking-tight">Notes from Patch</h2>
              <p className="mt-2 text-sm leading-relaxed text-muted">
                Patch leaves a note when a repository becomes presentable, ships a release, gets a live link or
                passes a star milestone. Pitch asks Patch for the facts and says whether there is enough for a
                post: the repository has to say what it is and score 80 or more. Only the facts listed under a
                note may appear in a post.
              </p>
              {notes.length > 0 && (
                <p className="mt-3 text-sm">
                  {plural(notes.length, "note")}. Enough for a post: <span className="font-semibold">{ready}</span>.
                </p>
              )}
            </div>

            {notes.length ? (
              <ul className="flex flex-col gap-4">
                {notes.map((note) => (
                  <NoteCard key={note.id} note={note} />
                ))}
              </ul>
            ) : (
              <p className="panel p-8 text-sm leading-relaxed text-muted">
                No notes yet. The first audit is a baseline, so nothing in it is news. A note appears when
                something changes after that.
              </p>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
