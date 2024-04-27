"use client";

import { Ban, Check } from "lucide-react";
import { useState, type FormEvent } from "react";
import { AgentCard } from "@/components/AgentCard";
import { Lessons } from "@/components/Lessons";
import { NoteCard } from "@/components/Notes";
import { PlainPost } from "@/components/PlainPost";
import { PostArea } from "@/components/PostEditor";
import { useStream } from "@/components/StreamProvider";
import { Button, InlineError, Notice } from "@/components/ui";
import { agentMeta } from "@/lib/agents";
import { fetchNotes, fetchPickable, fetchPitch, fetchPosts, pickRepo, setTone, writePost } from "@/lib/api";
import { agentView, lastFinishedRun, lastOfType } from "@/lib/feed";
import { plural } from "@/lib/format";
import { SHOWCASE } from "@/lib/showcase";
import type { Handoff, PickableRepo, PitchSheet, Post } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { setViewerTone, useViewerTone } from "@/lib/viewerTone";

const LESSON_COPY = {
  title: "What Pitch has learned",
  intro:
    "When you change a draft before posting it, or pass on one and say why, Pitch turns that into one short rule. " +
    "Rules that are switched on go to Claude with every draft. Reword, switch off or delete any of them, or add " +
    "your own.",
  empty: "Nothing yet. The first rule appears after you edit a draft and post it.",
  placeholder: "Teach Pitch a rule, for example: Never use hashtags.",
};

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

/**
 * Ask for a post about a repository Patch left no note for. `onPick` does it: on the working
 * desk it leaves Pitch a note and asks for the draft, and on the view-only copy it opens the
 * repository's card on this page.
 */
function Pick({ repos, onPick }: { repos: PickableRepo[]; onPick: (item: PickableRepo) => Promise<void> }) {
  const [repo, setRepo] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!repos.length) return null;
  const chosen = repos.find((item) => item.repo === repo);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!chosen) return;
    setBusy(true);
    setError(null);
    try {
      await onPick(chosen);
      setRepo("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-2 border-t border-line pt-4">
      <label htmlFor="pick-repo" className="text-sm leading-relaxed text-muted">
        Want a post about something Patch has not flagged? Pick a repository, then press the button.
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <select
          id="pick-repo"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          className="min-w-0 flex-1 rounded-xl border border-line-strong bg-sunken px-3 py-2 text-sm"
        >
          <option value="">Choose a repository</option>
          {repos.map((item) => (
            <option key={item.repo} value={item.repo}>
              {item.name}
              {item.ready ? "" : " (not ready yet)"}
            </option>
          ))}
        </select>
        <Button type="submit" variant={repo ? "primary" : "ghost"} disabled={busy || !repo}>
          {busy ? "Starting" : chosen && !chosen.ready ? "Add a note" : "Write the post"}
        </Button>
      </div>
      {chosen && !chosen.ready && chosen.reason && (
        <p className="text-xs leading-relaxed text-faint">Pitch says not yet. {chosen.reason}</p>
      )}
      {error && <InlineError>{error}</InlineError>}
    </form>
  );
}

/**
 * The tone your posts are written in. On the working desk the choice is kept by Pitch, and until
 * you make one a draft comes in every tone. On the view-only copy the choice is kept in the
 * visitor's own browser, and decides which version a post opens in.
 */
function Tone({ sheet, onChanged }: { sheet: PitchSheet; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const viewerTone = useViewerTone();
  const tones = sheet.tones ?? {};
  const tone = SHOWCASE ? viewerTone : (sheet.tone ?? null);
  if (!Object.keys(tones).length) return null; // a snapshot from before Pitch could write

  async function choose(next: string | null) {
    if (SHOWCASE) return setViewerTone(next);
    setBusy(true);
    setError(null);
    try {
      await setTone(next);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    } finally {
      setBusy(false);
    }
  }

  const pill = (on: boolean) =>
    `rounded-full px-3 py-1.5 text-sm transition disabled:opacity-50 ${
      on ? "bg-fg text-bg" : "border border-line-strong text-muted hover:text-fg"
    }`;

  return (
    <section className="panel flex flex-col gap-3 p-6" aria-label="Your tone">
      <div>
        <h2 className="font-display text-xl font-semibold tracking-tight">Your tone</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          {SHOWCASE
            ? tone
              ? `Posts open in the ${tones[tone]?.label.toLowerCase() ?? tone} version. The choice is kept in this browser.`
              : "Pick the tone you want posts in. Every post here comes in all three, and your choice decides which one it opens in."
            : tone
              ? `Drafts are written in the ${tones[tone]?.label.toLowerCase() ?? tone} tone, with two other opening lines to choose from.`
              : "Not chosen yet. A draft comes in every tone, and the one you post becomes yours."}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        {Object.entries(tones).map(([key, value]) => (
          <button key={key} type="button" aria-pressed={tone === key} disabled={busy} onClick={() => choose(key)} className={pill(tone === key)}>
            {value.label}
          </button>
        ))}
        <button type="button" aria-pressed={tone === null} disabled={busy} onClick={() => choose(null)} className={pill(tone === null)}>
          {SHOWCASE ? "No preference" : "Every tone"}
        </button>
      </div>
      <dl className="flex flex-col gap-1.5 text-xs leading-relaxed text-faint">
        {Object.entries(tones).map(([key, value]) => (
          <div key={key}>
            <dt className="inline font-medium text-muted">{value.label}: </dt>
            <dd className="inline">{value.how}</dd>
          </div>
        ))}
      </dl>
      {SHOWCASE && (
        <p className="text-xs leading-relaxed text-faint">
          On this copy, rules write each version from the facts. On the working desk Claude writes them, and
          the tone its owner posts in becomes the one later drafts are written in.
        </p>
      )}
      {error && <InlineError>{error}</InlineError>}
    </section>
  );
}

/** A repository a visitor picked on the view-only copy, and when. It lives in this page only. */
type Picked = { repo: string; at: string };

export default function PitchPage() {
  const { events, status } = useStream();
  const [version, setVersion] = useState(0);
  const changed = () => setVersion((v) => v + 1);
  // Re-read when an agent's status changes or a run finishes: either can change what a note is worth.
  const key = `${status}:${version}:${lastOfType(events, "agent.status")}:${lastFinishedRun(events)}`;
  const { data: sheet, error } = useApi(`pitch:${key}`, fetchPitch);
  const { data: tray } = useApi(`notes:${key}`, fetchNotes);
  // Drafts exist on the working desk only. The view-only copy never holds one.
  const { data: mine } = useApi(`posts:${key}`, (signal) => (SHOWCASE ? Promise.resolve(null) : fetchPosts(signal)));
  // The projects with no note yet. A snapshot from before they could be picked has no such list.
  const { data: pickable } = useApi(`pickable:${key}`, (signal) => fetchPickable(signal).catch(() => null));
  const [picked, setPicked] = useState<Picked[]>([]);
  const meta = agentMeta("pitch");

  // On the view-only copy a pick is not sent anywhere. It becomes a card on this page, read
  // exactly as Pitch would read a note about it, and the newest pick goes on top.
  const pickedNotes: Handoff[] = [];
  for (const [index, pick] of picked.entries()) {
    const item = pickable?.repos.find((candidate) => candidate.repo === pick.repo);
    if (!item?.brief) continue;
    pickedNotes.push({
      id: -(index + 1),
      ts: pick.at,
      repo: item.repo,
      from: "you",
      to: "pitch",
      topic: "pick",
      text: `You asked for a post about ${item.name}.`,
      data: {},
      thread: `pick:${item.repo}`,
      brief: item.brief,
    });
  }
  pickedNotes.reverse();
  const unpicked = (pickable?.repos ?? []).filter((item) => !picked.some((pick) => pick.repo === item.repo));

  async function onPick(item: PickableRepo) {
    if (SHOWCASE) {
      setPicked((all) => [...all, { repo: item.repo, at: new Date().toISOString() }]);
      return;
    }
    // One press does both: leave the note, and if there is enough for a post, ask for the draft.
    const note = await pickRepo(item.repo);
    if (item.ready) await writePost(note.id);
    changed();
  }

  // What can be posted comes first. Within each group the newest note stays on top.
  const all = [...pickedNotes, ...(tray?.handoffs ?? [])];
  const notes = [...all.filter((note) => note.brief?.ready), ...all.filter((note) => !note.brief?.ready)];
  const ready = all.filter((note) => note.brief?.ready).length;
  const view = agentView(events, "pitch", sheet?.status ?? { status: "idle", text: "Nothing new worth a post.", mood: "normal" });

  // The newest post written for each note. The list comes newest first.
  const posts = new Map<string, Post>();
  for (const post of mine?.posts ?? []) if (!posts.has(post.thread)) posts.set(post.thread, post);

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
                  It reads Patch&apos;s notes with rules only. It writes a draft when you ask, in one Claude call.
                  It has no access to LinkedIn: you copy the draft and press Post yourself.
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
                This is how Pitch talks on this site. A post goes out under your name, so it is written as you. To
                change the voice, edit backend/app/agents/linkedin/persona.toml.
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
              {SHOWCASE && (
                <p className="mt-3 text-xs leading-relaxed text-faint">
                  This copy has no Claude, so Write the post gives the post in three tones as rules write it
                  from the facts. On the working desk Claude writes them, and those drafts stay there.
                </p>
              )}
              {pickable && <Pick repos={unpicked} onPick={onPick} />}
            </div>

            {notes.length ? (
              <ul className="flex flex-col gap-4">
                {notes.map((note) => (
                  <NoteCard key={note.id} note={note}>
                    {mine ? (
                      <PostArea
                        note={note}
                        post={note.thread ? posts.get(note.thread) : undefined}
                        writing={mine.writing.includes(note.id)}
                        claudeOff={mine.claude_off}
                        rechecks={mine.claude_rechecks === true}
                        onChanged={changed}
                      />
                    ) : (
                      // A repository picked on this page was picked to be written about: open it.
                      SHOWCASE && <PlainPost note={note} startOpen={note.id < 0} />
                    )}
                  </NoteCard>
                ))}
              </ul>
            ) : (
              <p className="panel p-8 text-sm leading-relaxed text-muted">
                No notes yet. The first audit is a baseline, so nothing in it is news. A note appears when
                something changes after that, or when you pick a repository above.
              </p>
            )}

            <Tone sheet={sheet} onChanged={changed} />
            {SHOWCASE ? (
              <section className="panel flex flex-col gap-3 p-6" aria-label="Lessons">
                <h2 className="font-display text-xl font-semibold tracking-tight">What Pitch has learned</h2>
                <p className="text-sm leading-relaxed text-muted">
                  On the working desk, an edit made before posting, or a reason given for passing on a draft,
                  becomes one short rule that goes to Claude with every later draft. A rule can repeat what a
                  draft said, so the rules stay on the desk and are not shown here.
                </p>
              </section>
            ) : (
              <Lessons
                agent="pitch"
                copy={LESSON_COPY}
                lessons={sheet.lessons ?? []}
                limit={sheet.lesson_limit ?? 12}
                onChanged={changed}
              />
            )}
          </section>
        </div>
      )}
    </div>
  );
}
