"use client";

import { useEffect, useEffectEvent, useState } from "react";

type State<T> = { data: T | null; error: string | null; loading: boolean };

/**
 * Load something from the API and reload it whenever `key` changes.
 * Pages pass the id of the last finished run in the key, so they refresh when Patch finishes work.
 */
export function useApi<T>(key: string, load: (signal: AbortSignal) => Promise<T>): State<T> {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: true });
  const run = useEffectEvent(load);

  useEffect(() => {
    const controller = new AbortController();
    run(controller.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message = error instanceof Error ? error.message : "The request failed.";
        setState((previous) => ({ ...previous, error: message, loading: false }));
      });
    return () => controller.abort();
  }, [key]);

  return state;
}
