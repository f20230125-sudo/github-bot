"use client";

import { useEffect, useState } from "react";
import { API_URL } from "./api";
import { loadSnapshot, setCursor, SHOWCASE } from "./showcase";
import { EVENT_TYPES, type DeskEvent, type EventType } from "./types";

export type StreamStatus = "connecting" | "live" | "offline";

/** Controls for the recording, in the view-only build. */
export type Replay = { playing: boolean; restart: () => void; finish: () => void };

const MAX_EVENTS = 400;
/** Things worth knowing live but not worth storing: a check that found nothing, the pause switch. */
const SIGNALS = ["heartbeat", "desk"];

/** How long each kind of step stays on screen before the next one, when the recording plays. */
const STEP_MS: Partial<Record<EventType, number>> = {
  "run.started": 350,
  "run.step": 420,
  "tool.result": 70,
  finding: 70,
  "proposal.created": 220,
  message: 520,
  "agent.status": 150,
  usage: 80,
  "run.finished": 450,
};

const WATCHED_KEY = "desk-watched-recording";

/** Which recording this browser has already played to the end, if any. */
function watched(): string | null {
  try {
    return localStorage.getItem(WATCHED_KEY);
  } catch {
    return null;
  }
}

function remember(version: string): void {
  try {
    localStorage.setItem(WATCHED_KEY, version);
  } catch {
    /* without storage the recording simply plays on every visit */
  }
}

/**
 * Live feed from the API's server-sent events. The browser reconnects by itself and sends
 * Last-Event-ID, so the server resumes where the page left off and nothing is shown twice.
 *
 * `pulse` goes up each time a signal arrives, so anything showing the desk's state can re-read it.
 *
 * The view-only build has no API to listen to. There, the stored events of the snapshot are
 * played back in order, at a readable pace, as if they were arriving live.
 */
export function useEventStream() {
  const [events, setEvents] = useState<DeskEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [pulse, setPulse] = useState(0);
  // The view-only build: which playthrough this is, and whether to jump straight to the end.
  const [take, setTake] = useState({ n: 0, toEnd: false });
  const [playing, setPlaying] = useState(false);

  useEffect(() => {
    if (SHOWCASE) return;
    const source = new EventSource(`${API_URL}/api/stream`);

    const onEvent = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as DeskEvent;
      setEvents((prev) => (prev.some((p) => p.id === event.id) ? prev : [...prev, event].slice(-MAX_EVENTS)));
    };
    const onSignal = () => setPulse((n) => n + 1);

    for (const type of EVENT_TYPES) source.addEventListener(type, onEvent as EventListener);
    for (const name of SIGNALS) source.addEventListener(name, onSignal);
    source.onopen = () => setStatus("live");
    source.onerror = () => setStatus(source.readyState === EventSource.CLOSED ? "offline" : "connecting");

    return () => source.close();
  }, []);

  useEffect(() => {
    if (!SHOWCASE) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    loadSnapshot()
      .then(({ events: recorded, exported_at: version }) => {
        if (cancelled) return;
        setStatus("live");

        const end = () => {
          setCursor(null);
          setEvents(recorded);
          setPlaying(false);
          setPulse((n) => n + 1);
          remember(version);
        };
        // No animation for people who asked their system for less motion, and none for someone
        // who has already watched this recording: they get the result straight away.
        const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const seen = take.n === 0 && watched() === version;
        if (take.toEnd || still || seen || recorded.length === 0) return end();

        let shown = 0;
        setCursor({ id: 0, ts: "" });
        setEvents([]);
        setPlaying(true);
        const step = () => {
          if (cancelled) return;
          if (shown >= recorded.length) return end();
          const event = recorded[shown++];
          setCursor(event);
          setEvents(recorded.slice(0, shown));
          timer = setTimeout(step, STEP_MS[event.type] ?? 200);
        };
        step();
      })
      .catch(() => {
        if (!cancelled) setStatus("offline");
      });

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [take]);

  const replay: Replay | null = SHOWCASE
    ? {
        playing,
        restart: () => setTake((t) => ({ n: t.n + 1, toEnd: false })),
        finish: () => setTake((t) => ({ n: t.n + 1, toEnd: true })),
      }
    : null;

  return { events, status, pulse, replay };
}
