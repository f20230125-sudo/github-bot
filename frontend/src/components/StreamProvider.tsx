"use client";

import { createContext, useContext, type ReactNode } from "react";
import { fetchDesk } from "@/lib/api";
import { lastOfType } from "@/lib/feed";
import type { DeskEvent, DeskState } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { useEventStream, type Replay, type StreamStatus } from "@/lib/useEventStream";

type StreamState = {
  events: DeskEvent[];
  status: StreamStatus;
  /** Who works here, whether the desk is paused, and when Patch last checked GitHub. */
  desk: DeskState | null;
  /** Only in the view-only build: controls for the recording that plays in place of a live feed. */
  replay: Replay | null;
};

const StreamContext = createContext<StreamState>({ events: [], status: "connecting", desk: null, replay: null });

/** One live connection shared by the header and every page. */
export function StreamProvider({ children }: { children: ReactNode }) {
  const { events, status, pulse, replay } = useEventStream();
  // Re-read the desk when the connection comes back, a signal arrives, or an agent's status changes.
  const { data: desk } = useApi(`desk:${status}:${pulse}:${lastOfType(events, "agent.status")}`, fetchDesk);
  return <StreamContext.Provider value={{ events, status, desk, replay }}>{children}</StreamContext.Provider>;
}

export const useStream = () => useContext(StreamContext);
