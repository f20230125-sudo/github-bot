"use client";

import { ArrowUpRight, Copy, PenLine } from "lucide-react";
import { useState } from "react";
import { composeUrl, LINKEDIN_CHARS } from "@/lib/linkedin";
import type { Handoff } from "@/lib/types";
import { useViewerTone } from "@/lib/viewerTone";
import { Button } from "./ui";

/**
 * "Write the post" on the view-only copy. There is no Claude here to ask, so the post comes in
 * each tone as rules write it from the facts. It can be changed, copied and taken to LinkedIn.
 * Nothing is saved: the text lives in this page until it is closed.
 */
export function PlainPost({ note, startOpen = false }: { note: Handoff; startOpen?: boolean }) {
  const variants = note.brief?.plain_posts ?? [];
  const preferred = useViewerTone();
  const [open, setOpen] = useState(startOpen);
  // The version chosen on this card. Until one is, the card follows the tone chosen for the site.
  const [picked, setPicked] = useState<string | null>(null);
  // Your text for each version. Switching versions keeps what you typed in each of them.
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(variants.map((variant) => [variant.tone, variant.text])),
  );
  const [said, setSaid] = useState<string | null>(null);
  if (!variants.length) return null;

  const tone = picked ?? (variants.some((variant) => variant.tone === preferred) ? preferred! : variants[0].tone);
  const text = texts[tone] ?? "";
  const drafted = variants.find((variant) => variant.tone === tone)?.text ?? "";
  const setText = (value: string) => setTexts((all) => ({ ...all, [tone]: value }));
  const picture = note.brief?.facts.find((fact) => fact.label === "Picture");
  const usable = text.trim().length > 0 && text.length <= LINKEDIN_CHARS;

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
        <Button variant="primary" onClick={() => setOpen(true)}>
          <PenLine size={14} aria-hidden />
          Write the post
        </Button>
        <span className="text-xs leading-relaxed text-faint">
          In {variants.length === 1 ? "one tone" : `${variants.length} tones`}, written by rules from the facts. This
          copy has no Claude.
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 border-t border-line pt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="eyebrow">The draft</h3>
        <span className="text-xs text-faint">Written by rules from the facts. No model call</span>
      </div>

      {variants.length > 1 && (
        <div role="tablist" aria-label="Versions" className="flex flex-wrap gap-2">
          {variants.map((variant) => (
            <button
              key={variant.tone}
              type="button"
              role="tab"
              aria-selected={variant.tone === tone}
              onClick={() => setPicked(variant.tone)}
              className={`rounded-full px-3 py-1.5 text-sm transition ${
                variant.tone === tone ? "bg-fg text-bg" : "border border-line-strong text-muted hover:text-fg"
              }`}
            >
              {variant.label}
            </button>
          ))}
        </div>
      )}

      <label htmlFor={`plain-${note.id}`} className="sr-only">
        The post
      </label>
      <textarea
        id={`plain-${note.id}`}
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
            Back to the written version
          </button>
        )}
      </p>
      {picture?.url && (
        <p className="text-sm leading-relaxed text-muted">
          Attach this picture when you post:{" "}
          <a href={picture.url} target="_blank" rel="noopener noreferrer" className="text-fg underline underline-offset-4">
            {picture.value}
          </a>
          . It can&apos;t be sent from here.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          disabled={!usable}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(text);
              setSaid("Copied. Paste it into a new post on LinkedIn.");
            } catch {
              setSaid("Couldn't copy it. Select the text and copy it yourself.");
            }
          }}
        >
          <Copy size={14} aria-hidden />
          Copy
        </Button>
        {/* A plain link, so the browser opens it as your own click. The text is copied on the way. */}
        <a
          href={usable ? composeUrl(text) : undefined}
          target="_blank"
          rel="noopener noreferrer"
          aria-disabled={!usable}
          onClick={() => {
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
      </div>
      {said && (
        <p role="status" className="text-xs leading-relaxed text-muted">
          {said}
        </p>
      )}
      <p className="text-xs leading-relaxed text-faint">
        Nothing here posts for you, and nothing you type is saved. On the working desk, Claude writes these
        versions, rules check them against the facts, and Pitch learns from your edits.
      </p>
    </div>
  );
}
