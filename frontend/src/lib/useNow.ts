"use client";

import { useSyncExternalStore } from "react";

const STEP_MS = 15_000;

function subscribe(onChange: () => void) {
  const id = setInterval(onChange, STEP_MS);
  return () => clearInterval(id);
}

/**
 * The current time, moving in 15-second steps, for "4 min ago" style text that keeps itself
 * up to date. It is null on the server and until the page is live, so the HTML sent matches
 * what the browser first renders.
 */
export function useNow(): number | null {
  return useSyncExternalStore(
    subscribe,
    () => Math.floor(Date.now() / STEP_MS) * STEP_MS,
    () => null,
  );
}
