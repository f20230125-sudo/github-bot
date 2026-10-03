"use client";

import { Check, Pencil, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { addLesson, deleteLesson, updateLesson } from "@/lib/api";
import { shortDate } from "@/lib/format";
import type { Lesson } from "@/lib/types";
import { Button, InlineError, Tag } from "./ui";

const SOURCE_LABEL: Record<Lesson["source"], string> = {
  rejection: "From a rejection",
  edit: "From your edit",
  you: "Written by you",
};

function Row({ lesson, onChanged }: { lesson: Lesson; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(lesson.text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
      setEditing(false);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="py-3">
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={lesson.active}
          disabled={busy}
          onChange={(e) => act(() => updateLesson(lesson.id, { active: e.target.checked }))}
          aria-label={lesson.active ? "Switch this lesson off" : "Switch this lesson on"}
          className="mt-1 size-4 shrink-0 accent-[var(--fg)]"
        />
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void act(() => updateLesson(lesson.id, { text }));
              }}
              className="flex flex-wrap items-center gap-2"
            >
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                maxLength={160}
                aria-label="Lesson"
                className="min-w-0 flex-1 rounded-xl border border-line-strong bg-sunken px-3 py-1.5 text-sm"
              />
              <Button type="submit" disabled={busy || !text.trim()} className="px-3 py-1.5">
                <Check size={14} aria-hidden />
                Save
              </Button>
              <Button
                onClick={() => {
                  setEditing(false);
                  setText(lesson.text);
                  setError(null);
                }}
                disabled={busy}
                className="px-3 py-1.5"
              >
                <X size={14} aria-hidden />
                Cancel
              </Button>
            </form>
          ) : (
            <p className={`text-sm leading-relaxed ${lesson.active ? "" : "text-faint line-through"}`}>{lesson.text}</p>
          )}
          <p className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-faint">
            <Tag>{SOURCE_LABEL[lesson.source]}</Tag>
            {shortDate(lesson.created_at)}
            {lesson.proposal_id !== null && (
              <Link href={`/proposals/${lesson.proposal_id}`} className="underline underline-offset-4 hover:text-fg">
                the decision
              </Link>
            )}
          </p>
          {error && (
            <p className="mt-2">
              <InlineError>{error}</InlineError>
            </p>
          )}
        </div>
        {!editing && (
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => setEditing(true)}
              disabled={busy}
              aria-label="Reword this lesson"
              className="grid size-8 place-items-center rounded-full text-muted transition hover:bg-surface-2 hover:text-fg"
            >
              <Pencil size={14} aria-hidden />
            </button>
            <button
              type="button"
              onClick={() => act(() => deleteLesson(lesson.id))}
              disabled={busy}
              aria-label="Delete this lesson"
              className="grid size-8 place-items-center rounded-full text-muted transition hover:bg-surface-2 hover:text-fg"
            >
              <Trash2 size={14} aria-hidden />
            </button>
          </div>
        )}
      </div>
    </li>
  );
}

/**
 * What Patch has learned from your decisions. A lesson that is switched on is sent to Claude
 * with every draft, so it changes what Patch writes from then on.
 */
export function Lessons({ lessons, limit, onChanged }: { lessons: Lesson[]; limit: number; onChanged: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = lessons.filter((lesson) => lesson.active).length;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await addLesson(text);
      setText("");
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't add that.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel flex flex-col gap-3 p-6" aria-label="Lessons">
      <div>
        <h2 className="font-display text-xl font-semibold tracking-tight">What Patch has learned</h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          When you reject a proposal and say why, or change a draft before approving it, Patch turns that into one
          short rule. Rules that are switched on go to Claude with every draft. Reword, switch off or delete any of
          them, or add your own.
        </p>
      </div>

      {lessons.length ? (
        <ul className="divide-y divide-line border-y border-line">
          {lessons.map((lesson) => (
            // Keyed on the text too, so a reworded lesson starts from its new wording.
            <Row key={`${lesson.id}:${lesson.text}`} lesson={lesson} onChanged={onChanged} />
          ))}
        </ul>
      ) : (
        <p className="border-y border-line py-4 text-sm text-muted">
          Nothing yet. The first lesson appears after you reject a proposal with a reason.
        </p>
      )}

      {active > limit && (
        <p className="text-xs leading-relaxed text-faint">
          {active} are switched on. Only the newest {limit} are sent, to keep each call small.
        </p>
      )}

      <form onSubmit={onSubmit} className="flex flex-wrap items-center gap-2">
        <label htmlFor="new-lesson" className="sr-only">
          A rule for Patch
        </label>
        <input
          id="new-lesson"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={160}
          placeholder="Teach Patch a rule, for example: Keep descriptions under 100 characters."
          className="min-w-0 flex-1 rounded-xl border border-line-strong bg-sunken px-3 py-2 text-sm placeholder:text-faint"
        />
        <Button type="submit" disabled={busy || text.trim().length < 8}>
          Add
        </Button>
      </form>
      {error && <InlineError>{error}</InlineError>}
    </section>
  );
}
