"use client";

import { useEffect, useState } from "react";
import { shortDate } from "@/lib/format";
import { loadSnapshot, REPO_URL, SHOWCASE } from "@/lib/showcase";

/** Shown on every page of the view-only build, so nobody takes the recording for a live system. */
export function ShowcaseBanner() {
  const [taken, setTaken] = useState<string | null>(null);

  useEffect(() => {
    if (!SHOWCASE) return;
    loadSnapshot()
      .then((snapshot) => setTaken(shortDate(snapshot.exported_at)))
      .catch(() => undefined);
  }, []);

  if (!SHOWCASE) return null;
  return (
    <p className="border-b border-line bg-surface px-4 py-2.5 text-center text-xs leading-relaxed text-muted sm:px-8">
      <span className="font-medium text-fg">View-only demo.</span> This is a recording of Patch auditing real
      repositories{taken && `, taken on ${taken}`}. Nothing here can change anything. The working desk runs on its
      owner&apos;s computer.{" "}
      <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className="text-fg underline underline-offset-4">
        See the code
      </a>
    </p>
  );
}
