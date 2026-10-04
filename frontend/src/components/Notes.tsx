import { shortDate, shortRepo } from "@/lib/format";
import type { Brief, Handoff } from "@/lib/types";
import { Dot, Tag } from "./ui";

const TOPIC_LABEL: Record<string, string> = {
  new_repo: "New repository",
  release: "Release",
  demo_link: "Live link",
  stars: "Stars",
  ready: "Presentable",
};

/** What kind of news the note is, which repository, and when Patch left it. */
function NoteHead({ note }: { note: Handoff }) {
  return (
    <p className="flex flex-wrap items-center gap-2">
      <Tag>{TOPIC_LABEL[note.topic] ?? note.topic}</Tag>
      {note.repo && <span className="font-mono text-xs">{shortRepo(note.repo)}</span>}
      <time className="text-xs text-faint" dateTime={note.ts}>
        {shortDate(note.ts)}
      </time>
    </p>
  );
}

/** Pitch's verdict on a note. The mark sits beside the words, so colour is never the only signal. */
export function Verdict({ brief }: { brief: Brief }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm">
      <Dot status={brief.ready ? "good" : "neutral"} />
      <span className="font-medium">{brief.verdict}</span>
      {brief.ready && <span className="text-xs text-faint">{brief.angle_label}</span>}
    </span>
  );
}

/** A note in the Floor's tray: what Patch said, and Pitch's verdict in one line. */
export function NoteLine({ note }: { note: Handoff }) {
  return (
    <li className="text-sm leading-relaxed">
      <NoteHead note={note} />
      <p className="mt-1 text-muted">{note.text}</p>
      {note.brief && (
        <p className="mt-1.5">
          <Verdict brief={note.brief} />
        </p>
      )}
    </li>
  );
}

/** A note with everything Pitch read out of it: the verdict, why, and what a post may state. */
export function NoteCard({ note }: { note: Handoff }) {
  const brief = note.brief;
  return (
    <li className="panel flex flex-col gap-4 p-5">
      <div>
        <NoteHead note={note} />
        <p className="mt-2 text-sm leading-relaxed text-muted">
          <span className="mr-2 text-xs text-faint">Patch</span>
          {note.text}
        </p>
      </div>

      {brief && (
        <>
          <div className="border-t border-line pt-4">
            <Verdict brief={brief} />
            {brief.missing.length > 0 && (
              <ul className="mt-2 flex flex-col gap-1 text-sm leading-relaxed text-muted">
                {brief.missing.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
          </div>

          {brief.facts.length > 0 && (
            <div>
              <h3 className="eyebrow">What a post may state</h3>
              <dl className="mt-2 divide-y divide-line border-y border-line text-sm">
                {brief.facts.map((fact) => (
                  <div key={fact.label} className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3 py-2">
                    <dt className="text-muted">{fact.label}</dt>
                    <dd className="min-w-0 break-words leading-relaxed">
                      {fact.url ? (
                        <a
                          href={fact.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="underline underline-offset-4"
                        >
                          {fact.value}
                        </a>
                      ) : (
                        fact.value
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {brief.wanted.length > 0 && (
            <div>
              <h3 className="eyebrow">Would make it better</h3>
              <ul className="mt-2 flex flex-col gap-1 text-sm leading-relaxed text-muted">
                {brief.wanted.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </li>
  );
}
