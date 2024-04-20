/**
 * The Claude section of the view-only copy. There is no Claude here to connect or test, so it
 * says how the working desk uses it.
 */
export function ClaudeAbout() {
  return (
    <section className="panel flex flex-col gap-3 p-6" aria-label="Claude">
      <h2 className="font-display text-xl font-semibold tracking-tight">Claude</h2>
      <p className="text-sm leading-relaxed text-muted">
        On the working desk, Patch and Pitch call the Claude Code program signed in on its owner&apos;s computer.
        So they use that Claude plan and never an API key. They stop calling Claude once plan usage reaches 40% of
        the 5-hour or the weekly limit, and they do not call at all when the usage cannot be read.
      </p>
      <p className="text-sm leading-relaxed text-muted">
        Each call is stripped down: no tools, no project files, a short system prompt. In the first live test on
        the owner&apos;s machine, on 4 October 2026, reading the usage cost nothing and a stripped-down call took
        1,712 tokens.
      </p>
      <p className="text-sm leading-relaxed text-muted">
        The scheduled check that keeps this site up to date never calls Claude. It uses rules and templates only,
        so what it suggests is limited to what those can write: licenses, .gitignore files, CI workflows and the
        plain version of a post.
      </p>
    </section>
  );
}
