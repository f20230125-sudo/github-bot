/** A small face whose expression follows the agent's mood (computed from real state, not random). */
const MOUTHS: Record<string, string> = {
  focused: "M18 31h12",
  unimpressed: "M18 32h8l4-1",
  satisfied: "M17 30q7 6 14 0",
  irritated: "M17 34q7-6 14 0",
  proud: "M16 29q8 8 16 0",
};

const BROWS: Record<string, string | null> = {
  focused: null,
  unimpressed: "M15 15h7M26 14h7",
  satisfied: null,
  irritated: "M15 13l7 3M33 13l-7 3",
  proud: "M15 14q3-3 7-1M26 13q4-2 7 1",
};

export function Face({ mood, color, size = 56 }: { mood: string; color: string; size?: number }) {
  const key = mood in MOUTHS ? mood : "focused";
  const brows = BROWS[key];
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" role="img" aria-label={`Mood: ${key}`}>
      <circle cx="24" cy="24" r="22" fill="var(--surface-2)" stroke={color} strokeWidth="2" />
      <circle cx="18" cy="21" r="2.4" fill="var(--fg)" />
      <circle cx="30" cy="21" r="2.4" fill="var(--fg)" />
      {brows && <path d={brows} stroke="var(--fg)" strokeWidth="1.8" strokeLinecap="round" fill="none" />}
      <path d={MOUTHS[key]} stroke="var(--fg)" strokeWidth="2" strokeLinecap="round" fill="none" />
    </svg>
  );
}
