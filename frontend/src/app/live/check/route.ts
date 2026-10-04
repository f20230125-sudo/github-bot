import { connection, github, latestRun, WORKFLOW, type Run } from "@/lib/server/github";

// Always answered at request time: it reports on, and starts, a job that runs elsewhere.
export const dynamic = "force-dynamic";

/**
 * Anyone who can open the site can press "Check now", so a check is never started while one is
 * running, or within this long of the last one finishing. A check is cheap (rules only, about
 * half a minute), and this keeps it from being started over and over.
 */
const COOLDOWN_MS = 120_000;

function view(run: Run | null) {
  if (!run) return { configured: true, state: "idle", at: null, ok: null, url: null, run_id: null };
  const running = run.status !== "completed";
  return {
    configured: true,
    state: running ? "running" : "idle",
    at: run.updated_at,
    ok: running ? null : run.conclusion === "success",
    url: run.html_url,
    run_id: run.id,
  };
}

/** How the scheduled check last went, and whether one is running now. */
export async function GET() {
  const link = connection();
  if (!link) return Response.json({ configured: false });
  try {
    return Response.json(view(await latestRun(link)));
  } catch {
    return Response.json({ configured: true, state: "unknown" }, { status: 502 });
  }
}

/** Start a check now, unless one is running or has only just finished. */
export async function POST() {
  const link = connection();
  if (!link) return Response.json({ configured: false }, { status: 501 });
  try {
    const run = await latestRun(link);
    if (run && run.status !== "completed") {
      // Someone already started one. Whoever asked can wait for that run instead.
      return Response.json({ ...view(run), started: false, reason: "running", since: run.created_at }, { status: 202 });
    }
    const sinceLast = run ? Date.now() - Date.parse(run.updated_at) : Infinity;
    if (sinceLast < COOLDOWN_MS) {
      const wait = Math.ceil((COOLDOWN_MS - sinceLast) / 1000);
      return Response.json({ ...view(run), started: false, reason: "recent", retry_in: wait });
    }

    const since = new Date().toISOString();
    const started = await github(link, `/actions/workflows/${WORKFLOW}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: link.branch }),
    });
    if (started.status !== 204) {
      return Response.json({ configured: true, started: false, reason: "refused", status: started.status }, { status: 502 });
    }
    return Response.json({ configured: true, state: "running", started: true, since }, { status: 202 });
  } catch {
    return Response.json({ configured: true, started: false, reason: "unreachable" }, { status: 502 });
  }
}
