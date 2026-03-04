/**
 * A small face whose expression follows the agent's mood. The mood is worked out from the health
 * of the repositories: happy at a portfolio score of 80 or more, sad under 60, normal in between.
 */
const MOUTHS: Record<string, string> = {
  happy: "M16 29q8 8 16 0",
  normal: "M18 31h12",
  sad: "M17 34q7-6 14 0",
};

const BROWS: Record<string, string | null> = {
  happy: null,
  normal: null,
  sad: "M15 16l7-3M33 16l-7-3",
};

export function Face({ mood, color, size = 56 }: { mood: string; color: string; size?: number }) {
  // A mood this build doesn't know (an older recording used other names) gets the plain face.
  const key = mood in MOUTHS ? mood : "normal";
  const brows = BROWS[key];
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" role="img" aria-label={`Mood: ${key}`} className="shrink-0">
      <circle cx="24" cy="24" r="22" fill="var(--surface-2)" stroke={color} strokeWidth="2" />
      <circle cx="18" cy="21" r="2.4" fill="var(--fg)" />
      <circle cx="30" cy="21" r="2.4" fill="var(--fg)" />
      {brows && <path d={brows} stroke="var(--fg)" strokeWidth="1.8" strokeLinecap="round" fill="none" />}
      <path d={MOUTHS[key]} stroke="var(--fg)" strokeWidth="2" strokeLinecap="round" fill="none" />
    </svg>
  );
}
