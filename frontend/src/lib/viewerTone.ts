"use client";

import { useSyncExternalStore } from "react";

/**
 * The tone a visitor chose for posts on the view-only copy. It is kept in this browser only: the
 * site has nowhere else to keep it, and nothing depends on it surviving.
 */
const KEY = "desk-tone";
const CHANGED = "desk-tone-changed";

let fallback: string | null = null; // used when the browser won't let the page store anything

function read(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return fallback;
  }
}

function subscribe(onChange: () => void) {
  window.addEventListener(CHANGED, onChange);
  window.addEventListener("storage", onChange); // another tab changed it
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** Null until a tone is chosen. Also null on the server and until the page is live, so the HTML sent matches. */
export function useViewerTone(): string | null {
  return useSyncExternalStore(subscribe, read, () => null);
}

export function setViewerTone(tone: string | null): void {
  fallback = tone;
  try {
    if (tone) localStorage.setItem(KEY, tone);
    else localStorage.removeItem(KEY);
  } catch {
    /* kept for this page only */
  }
  window.dispatchEvent(new Event(CHANGED));
}
