"use client";

import { ArrowUpRight, Check, Copy, PenLine, RotateCcw, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { dismissPost, markPosted, savePost, writePost } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { composeUrl, LINKEDIN_CHARS } from "@/lib/linkedin";
import type { Handoff, Post } from "@/lib/types";
import { Button, Dot, InlineError } from "./ui";

type Changed = () => void;

/** Runs one action at a time and keeps what went wrong, in words. */
function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : "That didn't go through.");
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, act };
}

/** A note with enough for a post and nothing written yet: the button that asks Pitch to write. */
function Ask({
  note,
  writing,
  claudeOff,
  rechecks,
  onChanged,
}: {
  note: Handoff;
  writing: boolean;
  claudeOff: string | null;
  rechecks: boolean;
  onChanged: Changed;
}) {
  const { busy, error, act } = useAction();
  const how = !claudeOff
    ? "One Claude call writes it. Rules check it against the facts before you see it."
    : rechecks
      ? "Claude was over your stop at the last reading. Pitch looks again when you ask, at no cost, and uses Claude if it may."
      : "Claude is off, so this will be a plain post built from the facts. No model call.";
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-4">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant="primary"
          disabled={busy || writing}
          onClick={() =>
            act(async () => {
              await writePost(note.id);
              onChanged();
            })
          }
        >
          <PenLine size={14} aria-hidden />
          {writing ? "Writing" : "Write the post"}
        </Button>
        <span className="text-xs leading-relaxed text-faint">{how}</span>
      </div>
      {claudeOff && <p className="text-xs leading-relaxed text-faint">{claudeOff}</p>}
      {error && <InlineError>{error}</InlineError>}
    </div>
  );
}

/** A draft waiting for you: pick a version, change it, copy it, post it yourself, say that you did. */
function Draft({ note, post, onChanged }: { note: Handoff; post: Post; onChanged: Changed }) {
  const [tone, setTone] = useState(post.tone ?? post.variants[0]?.tone ?? "plain");
  // Your text for each version. Switching versions keeps what you typed in each of them.
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(post.variants.map((v) => [v.tone, v.tone === post.tone && post.text ? post.text : v.text])),
  );
  const [said, setSaid] = useState<string | null>(null);
  const [passing, setPassing] = useState(false);
  const [reason, setReason] = useState("");
  const { busy, error, act } = useAction();

  const text = texts[tone] ?? "";
  const drafted = post.variants.find((v) => v.tone === tone)?.text ?? "";
  const yours = { text, tone };
  const usable = text.trim().length > 0 && text.length <= LINKEDIN_CHARS;
  const setText = (value: string) => setTexts((all) => ({ ...all, [tone]: value }));

  /** Swap the first line for another opening, keeping the rest. */
  function openWith(hook: string) {
    const cut = text.indexOf("\n");
    setText(hook + (cut === -1 ? "" : text.slice(cut)));
  }

  async function copy() {
    await savePost(post.id, yours);
    await navigator.clipboard.writeText(text);
    setSaid("Copied. Paste it into a new post on LinkedIn.");
  }

  function onPass(event: FormEvent) {
    event.preventDefault();
    void act(async () => {
      await dismissPost(post.id, reason);
      onChanged();
    });
  }

  return (
    <div className="flex flex-col gap-3 border-t border-line pt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="eyebrow">The draft</h3>
        <span className="text-xs text-faint">
          {post.source === "claude"
            ? "Written by Claude in one call"
            : post.notes.length
              ? "Built from the facts by a template, because Claude's draft broke the rules"
              : "Built from the facts by a template. No model call"}
        </span>
      </div>

      {post.variants.length > 1 && (
        <div>
          <div role="tablist" aria-label="Versions" className="flex flex-wrap gap-2">
            {post.variants.map((variant) => (
              <button
                key={variant.tone}
                type="button"
                role="tab"
                aria-selected={variant.tone === tone}
                onClick={() => setTone(variant.tone)}
                className={`rounded-full px-3 py-1.5 text-sm transition ${
                  variant.tone === tone ? "bg-fg text-bg" : "border border-line-strong text-muted hover:text-fg"
                }`}
              >
                {variant.label}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs leading-relaxed text-faint">
            The same post in each tone. Pick the one that sounds like you: the one you post becomes your tone for
            later drafts.
          </p>
        </div>
      )}

      <label htmlFor={`post-${post.id}`} className="sr-only">
        The post
      </label>
      <textarea
        id={`post-${post.id}`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={Math.min(20, Math.max(8, text.split("\n").length + 3))}
        className="w-full rounded-xl border border-line-strong bg-sunken px-3 py-2.5 text-sm leading-relaxed"
      />
      <p className="flex flex-wrap items-center justify-between gap-2 text-xs text-faint">
        <span>
          <span className="tabular">{text.length}</span> of {LINKEDIN_CHARS} characters
          {text.length > LINKEDIN_CHARS && ". LinkedIn won't take that many."}
        </span>
        {text !== drafted && (
          <button type="button" onClick={() => setText(drafted)} className="underline underline-offset-4 hover:text-fg">
            Back to Pitch&apos;s version
          </button>
        )}
      </p>

      {post.hooks.length > 0 && (
        <div>
          <h4 className="eyebrow">Other opening lines</h4>
          <ul className="mt-2 flex flex-col gap-1.5">
            {post.hooks.map((hook) => (
              <li key={hook}>
                <button
                  type="button"
                  onClick={() => openWith(hook)}
                  className="rounded-xl border border-line px-3 py-2 text-left text-sm leading-relaxed text-muted transition hover:border-line-strong hover:text-fg"
                >
                  {hook}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {post.picture && (
        <p className="text-sm leading-relaxed text-muted">
          Attach this picture when you post:{" "}
          <a href={post.picture.url} target="_blank" rel="noopener noreferrer" className="text-fg underline underline-offset-4">
            {post.picture.path}
          </a>
          . It can&apos;t be sent from here.
        </p>
      )}
      {post.notes.length > 0 && (
        <p className="text-xs leading-relaxed text-faint">Thrown away before you saw it. {post.notes.join(". ")}.</p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => act(copy)} disabled={busy || !usable}>
          <Copy size={14} aria-hidden />
          Copy
        </Button>
        {/* A plain link, so the browser opens it as your own click. The text is saved and copied on the way. */}
        <a
          href={usable ? composeUrl(text) : undefined}
          target="_blank"
          rel="noopener noreferrer"
          aria-disabled={!usable}
          onClick={() => {
            void savePost(post.id, yours).catch(() => undefined);
            void navigator.clipboard.writeText(text).catch(() => undefined);
            setSaid(
              "LinkedIn opened a new post in another tab. The text is on your clipboard too: paste it if the post is empty. Press Post there.",
            );
          }}
          className={`inline-flex items-center gap-2 rounded-full border border-line-strong px-4 py-2 text-sm font-medium transition hover:bg-surface-2 ${
            usable ? "" : "pointer-events-none opacity-50"
          }`}
        >
          Open LinkedIn
          <ArrowUpRight size={14} aria-hidden />
        </a>
        <Button
          variant="primary"
          disabled={busy || !usable}
          onClick={() =>
            act(async () => {
              await markPosted(post.id, yours);
              onChanged();
            })
          }
        >
          <Check size={14} aria-hidden />I posted it
        </Button>
        <Button onClick={() => setPassing((open) => !open)} disabled={busy}>
          <X size={14} aria-hidden />
          Not this one
        </Button>
        <Button
          disabled={busy}
          onClick={() =>
            act(async () => {
              await writePost(note.id, true);
              onChanged();
            })
          }
        >
          <RotateCcw size={14} aria-hidden />
          Write it again
        </Button>
      </div>

      {passing && (
        <form onSubmit={onPass} className="flex flex-wrap items-center gap-2">
          <label htmlFor={`pass-${post.id}`} className="sr-only">
            Why not
          </label>
          <input
            id={`pass-${post.id}`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            maxLength={300}
            placeholder="Why not? Optional, and Pitch learns from it."
            className="min-w-0 flex-1 rounded-xl border border-line-strong bg-sunken px-3 py-2 text-sm placeholder:text-faint"
          />
          <Button type="submit" disabled={busy}>
            Drop the draft
          </Button>
        </form>
      )}
      {said && (
        <p role="status" className="text-xs leading-relaxed text-muted">
          {said}
        </p>
      )}
      <p className="text-xs leading-relaxed text-faint">
        Nothing here posts for you. &quot;I posted it&quot; only tells Pitch, so it can learn from what you changed.
      </p>
      {error && <InlineError>{error}</InlineError>}
    </div>
  );
}

/** What became of a draft: posted by you, or passed on. Either way another can be written. */
function Outcome({ note, post, onChanged }: { note: Handoff; post: Post; onChanged: Changed }) {
  const { busy, error, act } = useAction();
  const posted = post.status === "posted";
  return (
    <div className="flex flex-col gap-3 border-t border-line pt-4">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <Dot status={posted ? "good" : "neutral"} />
        <span className="font-medium">{posted ? "You posted this" : "You passed on the draft"}</span>
        <time className="text-xs text-faint" dateTime={post.updated_at}>
          {shortDate(post.updated_at)}
        </time>
      </p>
      {posted && post.text && (
        <p className="whitespace-pre-wrap rounded-xl border border-line bg-sunken px-3 py-2.5 text-sm leading-relaxed">
          {post.text}
        </p>
      )}
      {!posted && post.reason && <p className="text-sm leading-relaxed text-muted">{post.reason}</p>}
      <div>
        <Button
          disabled={busy}
          onClick={() =>
            act(async () => {
              await writePost(note.id, true);
              onChanged();
            })
          }
        >
          <RotateCcw size={14} aria-hidden />
          {posted ? "Write another" : "Write it again"}
        </Button>
      </div>
      {error && <InlineError>{error}</InlineError>}
    </div>
  );
}

/**
 * Under a note on the working desk: ask for a post, work on the draft, or see what became of it.
 * A note without enough for a post shows nothing here. Its card already says what is missing.
 */
export function PostArea({
  note,
  post,
  writing,
  claudeOff,
  rechecks,
  onChanged,
}: {
  note: Handoff;
  post: Post | undefined;
  writing: boolean;
  claudeOff: string | null;
  rechecks: boolean;
  onChanged: Changed;
}) {
  if (post?.status === "draft" && !writing) {
    // Keyed on the draft, so a rewritten one starts from its own text, not from what you typed before.
    return <Draft key={post.id} note={note} post={post} onChanged={onChanged} />;
  }
  if (post && !writing) return <Outcome note={note} post={post} onChanged={onChanged} />;
  if (!note.brief?.ready && !writing) return null;
  return <Ask note={note} writing={writing} claudeOff={claudeOff} rechecks={rechecks} onChanged={onChanged} />;
}
