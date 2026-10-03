"use client";

import { useEffect, useState } from "react";
import { API_URL } from "./api";
import { EVENT_TYPES, type DeskEvent } from "./types";

export type StreamStatus = "connecting" | "live" | "offline";

const MAX_EVENTS = 400;
/** Things worth knowing live but not worth storing: a check that found nothing, the pause switch. */
const SIGNALS = ["heartbeat", "desk"];

/**
 * Live feed from the API's server-sent events. The browser reconnects by itself and sends
 * Last-Event-ID, so the server resumes where the page left off and nothing is shown twice.
 *
 * `pulse` goes up each time a signal arrives, so anything showing the desk's state can re-read it.
 */
export function useEventStream() {
  const [events, setEvents] = useState<DeskEvent[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [pulse, setPulse] = useState(0);

  useEffect(() => {
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

  return { events, status, pulse };
}
