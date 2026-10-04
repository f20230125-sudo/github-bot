import { connection, github, SNAPSHOT_PATH } from "@/lib/server/github";

export const dynamic = "force-dynamic";

/**
 * The newest committed snapshot, read from the repository itself. A check that has just finished
 * shows at once this way. Without this, the site would read GitHub's file cache, which can lag
 * a new commit by several minutes.
 */
export async function GET() {
  const link = connection();
  if (!link) return Response.json({ configured: false }, { status: 501 });
  try {
    const file = await github(link, `/contents/${SNAPSHOT_PATH}?ref=${encodeURIComponent(link.branch)}`, {
      headers: { Accept: "application/vnd.github.raw+json" },
    });
    if (!file.ok) return Response.json({ configured: true, error: `GitHub answered ${file.status}` }, { status: 502 });
    return new Response(file.body, {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        // Shared between visitors: fresh for 15 seconds, then served once more while a new copy
        // is fetched, up to 45 seconds old. So the plain address can still answer with the copy
        // from before a check that has just finished. A page that knows of a newer one asks with
        // ?v=, which makes a new address that no older copy is stored under.
        "Cache-Control": "public, s-maxage=15, stale-while-revalidate=30",
      },
    });
  } catch {
    return Response.json({ configured: true, error: "GitHub could not be reached" }, { status: 502 });
  }
}
