"use client";

import { SunMoon } from "lucide-react";
import { useLayoutEffect } from "react";

const KEY = "desk-theme";

export function ThemeToggle() {
  // React's dev Strict Mode remount clears the attribute the inline script set; re-apply it.
  useLayoutEffect(() => {
    try {
      const stored = localStorage.getItem(KEY);
      if (stored === "light" || stored === "dark") document.documentElement.setAttribute("data-theme", stored);
    } catch {
      /* storage unavailable: keep the system theme */
    }
  }, []);

  function toggle() {
    const root = document.documentElement;
    const system = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    const next = (root.getAttribute("data-theme") ?? system) === "light" ? "dark" : "light";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* the choice just won't persist */
    }
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Switch between light and dark theme"
      className="grid size-9 place-items-center rounded-full border border-line text-muted transition-colors hover:border-line-strong hover:text-fg"
    >
      <SunMoon size={17} aria-hidden />
    </button>
  );
}
