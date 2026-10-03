import { statusColor } from "@/lib/format";

/**
 * A unified diff. Added and removed lines carry a + or - in the gutter and a faint wash of the
 * status colour; the text itself stays in ink, so the sign is the signal and colour only helps.
 */
export function Diff({ lines }: { lines: string[] }) {
  if (!lines.length) return <p className="px-4 py-3 text-sm text-faint">No changes.</p>;
  return (
    <div className="max-h-[520px] overflow-auto bg-sunken font-mono text-xs leading-relaxed" role="group" aria-label="Changes">
      {lines.map((line, index) => {
        const sign = line[0];
        const text = line.slice(1);
        if (sign === "@") {
          return (
            <p key={index} className="border-y border-line px-4 py-1 text-faint">
              {line}
            </p>
          );
        }
        const status = sign === "+" ? "good" : sign === "-" ? "critical" : null;
        return (
          <p
            key={index}
            className="flex gap-3 px-4"
            style={status ? { background: `color-mix(in oklab, ${statusColor(status)} 10%, transparent)` } : undefined}
          >
            <span className="w-3 shrink-0 select-none text-faint" aria-hidden={!status}>
              {status ? sign : ""}
            </span>
            <span className={`min-w-0 whitespace-pre-wrap break-words ${status ? "text-fg" : "text-muted"}`}>
              {text || " "}
            </span>
          </p>
        );
      })}
    </div>
  );
}
