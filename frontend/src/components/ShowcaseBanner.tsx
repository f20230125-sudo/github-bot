"use client";

import { useEffect, useState } from "react";
import { ago, shortDate } from "@/lib/format";
import {
  CHECK_EVERY,
  fetchScheduledCheck,
  loadSnapshot,
  REPO_URL,
  SHOWCASE,
  type ScheduledCheck,
} from "@/lib/showcase";
import { useNow } from "@/lib/useNow";

/**
 * Shown on every page of the view-only build: what keeps it up to date, when that last ran, and
 * that nothing here can change anything.
 */
export function ShowcaseBanner() {
  const [taken, setTaken] = useState<string | null>(null);
  const [check, setCheck] = useState<ScheduledCheck | null>(null);
  const now = useNow();

  useEffect(() => {
    if (!SHOWCASE) return;
    loadSnapshot()
      .then((snapshot) => setTaken(shortDate(snapshot.exported_at)))
      .catch(() => undefined);
    fetchScheduledCheck().then(setCheck);
  }, []);

  if (!SHOWCASE) return null;
  const link = "text-fg underline underline-offset-4";
  return (
    <p className="border-b border-line bg-surface px-4 py-2.5 text-center text-xs leading-relaxed text-muted sm:px-8">
      <span className="font-medium text-fg">View-only.</span> Patch checks these repositories by itself every{" "}
      {CHECK_EVERY}.{" "}
      {check && now !== null ? (
        <>
          {check.ok ? "Last check" : "The last check failed,"} {ago(check.at, now)}:{" "}
          <a href={check.url} target="_blank" rel="noopener noreferrer" className={link}>
            see the run
          </a>
          .{" "}
        </>
      ) : (
        taken && `What you see was recorded on ${taken}. `
      )}
      Nothing on this site changes anything.{" "}
      <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className={link}>
        See the code
      </a>
    </p>
  );
}
