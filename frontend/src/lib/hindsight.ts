// Handing a run to Hindsight, an observer for agents.
//
// The button opens Hindsight's /open page in a new tab. That page tells the tab
// that opened it that it is ready, and the run is handed over with postMessage,
// addressed to Hindsight alone, so no other page can read it. Nothing goes
// through a server. If the tab is blocked, or says nothing within five seconds,
// the run is saved as a file instead, which Hindsight can read from its
// Sources page.
//
// This file is the same in each of the apps that can send a run.

export const HINDSIGHT_URL = (process.env.NEXT_PUBLIC_HINDSIGHT_URL ?? "https://hindsight-sand.vercel.app").replace(/\/$/, "");

/** How long to wait for Hindsight's page to say it is ready. */
export const READY_TIMEOUT_MS = 5000;
/** How long to wait for it to say it has read the run. */
export const ANSWER_TIMEOUT_MS = 4000;

export type SendResult =
  /** Hindsight read the run and said so. */
  | { how: "sent" }
  /** The run was handed over, and no word came back. */
  | { how: "sent-unconfirmed" }
  /** Hindsight read the run and would not take it. */
  | { how: "refused"; reason: string }
  /** Hindsight could not be reached, so the run was saved as a file. */
  | { how: "file"; reason: "blocked" | "no-answer" };

export type WindowLike = { postMessage(message: unknown, targetOrigin: string): void };

/** Everything that comes from the browser, so a test can stand in for it. */
export type SendDeps = {
  /** Opens a page in a tab. Null when the browser blocked it. */
  open: (url: string) => WindowLike | null;
  /** Calls `handle` for every message the page receives. Returns a function that stops listening. */
  listen: (handle: (event: { origin: string; source: unknown; data: unknown }) => void) => () => void;
  /** Saves text as a file the visitor can keep. */
  save: (fileName: string, text: string) => void;
  setTimeout: (run: () => void, ms: number) => number;
  clearTimeout: (id: number) => void;
};

export function browserDeps(): SendDeps {
  return {
    // Called straight from the click, so the browser does not take it for a pop-up.
    open: (url) => window.open(url, "hindsight"),
    listen: (handle) => {
      const on = (event: MessageEvent) => handle({ origin: event.origin, source: event.source, data: event.data });
      window.addEventListener("message", on);
      return () => window.removeEventListener("message", on);
    },
    save: (fileName, text) => {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      link.download = fileName;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 10_000);
    },
    setTimeout: (run, ms) => window.setTimeout(run, ms),
    clearTimeout: (id) => window.clearTimeout(id),
  };
}

/**
 * Sends a run to Hindsight. Must be called from a click, before anything is
 * awaited, or the browser will block the new tab.
 */
export function sendToHindsight(envelope: object, fileName: string, deps: SendDeps = browserDeps(), url: string = HINDSIGHT_URL): Promise<SendResult> {
  const origin = new URL(url).origin;
  const target = deps.open(`${url}/open`);
  if (!target) {
    deps.save(fileName, JSON.stringify(envelope));
    return Promise.resolve({ how: "file", reason: "blocked" });
  }

  return new Promise((resolve) => {
    let handedOver = false;
    let timer = 0;

    const finish = (result: SendResult) => {
      deps.clearTimeout(timer);
      stop();
      resolve(result);
    };

    const stop = deps.listen((event) => {
      // Only Hindsight's own page, in the tab that was opened, is listened to.
      if (event.origin !== origin || event.source !== target) return;
      const message = event.data as { type?: unknown; reason?: unknown } | null;
      if (message?.type === "hindsight:ready" && !handedOver) {
        handedOver = true;
        deps.clearTimeout(timer);
        target.postMessage(envelope, origin);
        timer = deps.setTimeout(() => finish({ how: "sent-unconfirmed" }), ANSWER_TIMEOUT_MS);
      } else if (message?.type === "hindsight:received") {
        finish({ how: "sent" });
      } else if (message?.type === "hindsight:refused") {
        finish({ how: "refused", reason: typeof message.reason === "string" ? message.reason : "It could not be read." });
      }
    });

    timer = deps.setTimeout(() => {
      stop();
      deps.save(fileName, JSON.stringify(envelope));
      resolve({ how: "file", reason: "no-answer" });
    }, READY_TIMEOUT_MS);
  });
}

/** What to tell the visitor. */
export function describeSend(result: SendResult): { message: string; tone: "neutral" | "bad" } {
  switch (result.how) {
    case "sent":
      return { message: "Opened in Hindsight.", tone: "neutral" };
    case "sent-unconfirmed":
      return { message: "Sent to Hindsight. It did not say whether it could read it.", tone: "neutral" };
    case "refused":
      return { message: `Hindsight would not take it: ${result.reason}`, tone: "bad" };
    case "file":
      return {
        message:
          result.reason === "blocked"
            ? "The browser blocked the new tab, so the run was saved as a file. Drop it on Hindsight's Sources page."
            : "Hindsight did not answer, so the run was saved as a file. Drop it on Hindsight's Sources page.",
        tone: "neutral",
      };
  }
}
