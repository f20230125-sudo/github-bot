/**
 * The hosted site's one server-side connection to GitHub. Only route handlers import this.
 *
 * It needs PATCH_DISPATCH_TOKEN, set in the host's settings and never sent to a browser: a
 * fine-grained GitHub token limited to this one repository, with "Actions: read and write". That
 * lets it start the scheduled check and read how the last one went. It cannot touch any code.
 *
 * Without the token everything here reports "not configured", and the site falls back to what
 * it can do with no token at all.
 */

// GitHub's API. The override exists for GitHub Enterprise, and so the flow can be tried against a stand-in.
const API = (process.env.PATCH_GITHUB_API_URL || "https://api.github.com").replace(/\/$/, "");
export const WORKFLOW = "patch-watch.yml";
export const SNAPSHOT_PATH = "frontend/public/showcase/snapshot.json";

export type Connection = { token: string; repo: string; branch: string };

export function connection(): Connection | null {
  const token = process.env.PATCH_DISPATCH_TOKEN;
  const owner = process.env.VERCEL_GIT_REPO_OWNER;
  const name = process.env.VERCEL_GIT_REPO_SLUG;
  const repo = process.env.NEXT_PUBLIC_REPO || (owner && name ? `${owner}/${name}` : "");
  const branch = process.env.VERCEL_GIT_COMMIT_REF || "main";
  return token && repo ? { token, repo, branch } : null;
}

export function github(link: Connection, path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${API}/repos/${link.repo}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "agent-desk-site",
      ...init.headers,
      Authorization: `Bearer ${link.token}`,
    },
  });
}

export type Run = {
  id: number;
  status: string; // queued, in_progress, completed, ...
  conclusion: string | null;
  created_at: string;
  updated_at: string;
  html_url: string;
};

/** The newest run of the scheduled check, running or finished. */
export async function latestRun(link: Connection): Promise<Run | null> {
  const response = await github(link, `/actions/workflows/${WORKFLOW}/runs?per_page=1`);
  if (!response.ok) throw new Error(`GitHub answered ${response.status}`);
  const body = (await response.json()) as { workflow_runs?: Run[] };
  return body.workflow_runs?.[0] ?? null;
}
