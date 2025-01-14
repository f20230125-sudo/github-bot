"use client";

import { ArrowUp } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { agentMeta } from "@/lib/agents";
import { fetchChat, sendChat } from "@/lib/api";
import { clock } from "@/lib/format";
import type { ChatMessage, DeskEvent } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { useStream } from "./StreamProvider";
import { InlineError, Tag } from "./ui";

/** Commands answered by rules: they cost nothing, so they are one click away. */
const SHORTCUTS = ["/status", "/audit", "/draft", "/usage", "/help"];

/** Id of the newest event from a chat run. The thread reloads when it changes. */
function lastChatEvent(events: DeskEvent[]): number {
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].run_id?.startsWith("chat-")) return events[i].id;
  }
  return 0;
}

function Message({ message }: { message: ChatMessage }) {
  const mine = message.from === "you";
  const meta = agentMeta(message.from);
  return (
    <li className="px-5 py-3">
      <p className="flex items-center gap-2 text-xs text-faint">
        {!mine && <span className="inline-block size-2 rounded-full" style={{ background: meta.color }} aria-hidden />}
        <span className="font-medium text-muted">{mine ? "You" : meta.name}</span>
        <time className="tabular font-mono text-[11px]" dateTime={message.ts}>
          {clock(message.ts)}
        </time>
        {!mine && message.source && <Tag>{message.source === "claude" ? "Claude" : "No model call"}</Tag>}
      </p>
      <p className={`mt-1 text-sm leading-relaxed ${mine ? "text-muted" : ""}`}>{message.text}</p>
      {message.points.length > 0 && (
        <ul className="mt-2 flex list-disc flex-col gap-1 pl-5 text-sm leading-relaxed text-muted marker:text-faint">
          {message.points.map((point, i) => (
            <li key={i}>{point}</li>
          ))}
        </ul>
      )}
      {message.note && <p className="mt-2 text-xs leading-relaxed text-faint">{message.note}</p>}
    </li>
  );
}

/**
 * Talk to Patch. A command or a lookup is answered from stored data. Only an open question
 * reaches Claude, as one call, and only while your plan usage is under the stop.
 */
export function Chat() {
  const { events, status } = useStream();
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const thread = useRef<HTMLOListElement>(null);

  const { data } = useApi(`chat:${status}:${lastChatEvent(events)}`, fetchChat);
  const messages = data?.messages ?? [];
  const last = messages.at(-1);
  const waiting = sending || (data?.busy ?? false) || last?.from === "you";

  // Keep the newest message in view.
  useEffect(() => {
    const list = thread.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages.length, waiting]);

  async function send(message: string) {
    const trimmed = message.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setError(null);
    try {
      await sendChat(trimmed);
      setText("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't send that.");
    } finally {
      setSending(false);
    }
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void send(text);
  }

  const offline = status !== "live";
  return (
    <section className="panel overflow-hidden" aria-label="Talk to Patch">
      {messages.length > 0 && (
        <ol ref={thread} className="max-h-80 divide-y divide-line overflow-y-auto" aria-live="polite">
          {messages.map((message) => (
            <Message key={message.id} message={message} />
          ))}
          {waiting && last?.from === "you" && (
            <li className="flex items-center gap-2 px-5 py-3 text-xs text-faint">
              <span className="live-dot inline-block size-1.5 rounded-full bg-live" aria-hidden />
              Patch is on it.
            </li>
          )}
        </ol>
      )}

      <form onSubmit={onSubmit} className={`flex items-center gap-2 p-3 ${messages.length ? "border-t border-line" : ""}`}>
        <label htmlFor="chat-input" className="sr-only">
          Message for Patch
        </label>
        <input
          id="chat-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={1000}
          autoComplete="off"
          placeholder="Ask Patch about a repository, or type a command"
          className="min-w-0 flex-1 rounded-full bg-surface-2 px-4 py-2 text-sm placeholder:text-faint"
        />
        <button
          type="submit"
          disabled={!text.trim() || waiting || offline}
          aria-label="Send"
          className="grid size-9 shrink-0 place-items-center rounded-full bg-fg text-bg transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ArrowUp size={16} aria-hidden />
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-2.5">
        {SHORTCUTS.map((command) => (
          <button
            key={command}
            type="button"
            onClick={() => void send(command)}
            disabled={waiting || offline}
            className="rounded-full border border-line px-2.5 py-1 font-mono text-[11px] text-muted transition hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
          >
            {command}
          </button>
        ))}
        <span className="ml-auto text-xs text-faint">Commands and lookups use no model.</span>
      </div>
      {error && (
        <p className="border-t border-line px-5 py-2.5">
          <InlineError>{error}</InlineError>
        </p>
      )}
    </section>
  );
}
