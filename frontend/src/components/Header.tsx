"use client";

import { ArrowUpRight, Pause, Play } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { setPaused } from "@/lib/api";
import { REPO_URL, SHOWCASE } from "@/lib/showcase";
import { useStream } from "./StreamProvider";
import { ThemeToggle } from "./ThemeToggle";

const STATUS_COPY = {
  connecting: { label: "Connecting", dot: "bg-faint" },
  live: { label: "Live", dot: "bg-live live-dot" },
  paused: { label: "Paused", dot: "bg-warning" },
  offline: { label: "API offline", dot: "bg-critical" },
} as const;
const VIEW_ONLY = { label: "View-only demo", dot: "bg-neutral" };

const NAV = [
  { href: "/", label: "Floor" },
  { href: "/repos", label: "Repos" },
  { href: "/metrics", label: "Metrics", also: "/runs" },
  { href: "/agents/patch", label: "Patch" },
  { href: "/setup", label: "Setup" },
];

/** The kill switch. Pausing stops whatever is running and refuses new work until you resume. */
function PauseButton() {
  const { desk, status } = useStream();
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  const paused = desk?.paused ?? false;

  async function toggle() {
    setPending(true);
    setFailed(false);
    try {
      await setPaused(!paused);
    } catch {
      setFailed(true);
    } finally {
      setPending(false);
    }
  }

  const Icon = paused ? Play : Pause;
  return (
    <button
      type="button"
      onClick={toggle}
      disabled={pending || status !== "live" || !desk}
      title={
        failed
          ? "That didn't go through. Try again."
          : paused
            ? "Let the agents work again"
            : "Stop everything the agents are doing"
      }
      className={`flex shrink-0 items-center gap-2 rounded-full px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
        paused ? "bg-fg text-bg hover:opacity-90" : "border border-line-strong text-fg hover:bg-surface-2"
      }`}
    >
      <Icon size={13} aria-hidden />
      {paused ? "Resume" : "Pause"}
    </button>
  );
}

export function Header() {
  const { status, desk } = useStream();
  const pathname = usePathname();
  const s = SHOWCASE ? VIEW_ONLY : STATUS_COPY[status === "live" && desk?.paused ? "paused" : status];
  // The view-only copy has nothing to set up.
  const sections = SHOWCASE ? NAV.filter((item) => item.href !== "/setup") : NAV;

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/80 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-[1240px] items-center gap-2 px-4 sm:gap-4 sm:px-8">
        <Link href="/" className="flex shrink-0 items-center gap-3" aria-label="Agent Desk, home">
          <svg viewBox="-2 -2 36 36" className="size-7" aria-hidden>
            <path d="M16 1 29 23.5H3z" fill="none" stroke="currentColor" strokeWidth="3" strokeLinejoin="round" />
            <path d="M16 9.5 21.5 19h-11z" fill="currentColor" />
          </svg>
          <span className="hidden font-display text-lg font-semibold tracking-tight sm:inline">Agent Desk</span>
        </Link>

        {/* On a narrow screen the sections scroll sideways, so the pause switch never leaves the screen. */}
        <nav aria-label="Sections" className="no-scrollbar flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {sections.map(({ href, label, also }) => {
            const active =
              href === "/" ? pathname === "/" : pathname.startsWith(href) || (also ? pathname.startsWith(also) : false);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={`shrink-0 rounded-full px-3 py-1.5 text-sm transition-colors ${
                  active ? "bg-surface-2 text-fg" : "text-muted hover:text-fg"
                }`}
              >
                {label}
              </Link>
            );
          })}
        </nav>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          <span
            className="hidden items-center gap-2 rounded-full border border-line px-3 py-1.5 text-xs text-muted sm:flex"
            role="status"
          >
            <span className={`inline-block size-2 rounded-full ${s.dot}`} aria-hidden />
            {s.label}
          </span>
          {SHOWCASE ? (
            <a
              href={REPO_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex shrink-0 items-center gap-1.5 rounded-full border border-line-strong px-3 py-1.5 text-xs font-medium transition hover:bg-surface-2"
            >
              Code
              <ArrowUpRight size={13} aria-hidden />
            </a>
          ) : (
            <PauseButton />
          )}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
