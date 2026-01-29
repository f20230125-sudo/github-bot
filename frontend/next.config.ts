import type { NextConfig } from "next";

// Vercel says which repository and branch it is building.
const owner = process.env.VERCEL_GIT_REPO_OWNER;
const name = process.env.VERCEL_GIT_REPO_SLUG;
const branch = process.env.VERCEL_GIT_COMMIT_REF ?? "main";
const repo = owner && name ? `${owner}/${name}` : "";

const nextConfig: NextConfig = {
  env: {
    // "1" builds the view-only demo: no backend, every page reads public/showcase/snapshot.json.
    // A hosted copy has no backend to talk to, so on Vercel that is the default.
    // Set NEXT_PUBLIC_SHOWCASE yourself to override it either way.
    NEXT_PUBLIC_SHOWCASE: process.env.NEXT_PUBLIC_SHOWCASE ?? (process.env.VERCEL ? "1" : "0"),
    // The repository the site links to: its code, and the scheduled check that keeps it fresh.
    NEXT_PUBLIC_REPO: process.env.NEXT_PUBLIC_REPO ?? repo,
    // The scheduled check commits a new snapshot when something changed. A hosted copy reads it
    // from the repository, so it shows without a new deployment. Empty means: use the built-in copy.
    NEXT_PUBLIC_SNAPSHOT_URL:
      process.env.NEXT_PUBLIC_SNAPSHOT_URL ??
      (repo ? `https://raw.githubusercontent.com/${repo}/${branch}/frontend/public/showcase/snapshot.json` : ""),
  },
};

export default nextConfig;
