import type { NextConfig } from "next";

// Vercel says which repository and branch it is building.
const owner = process.env.VERCEL_GIT_REPO_OWNER;
const name = process.env.VERCEL_GIT_REPO_SLUG;
const branch = process.env.VERCEL_GIT_COMMIT_REF ?? "main";
const repo = owner && name ? `${owner}/${name}` : "";

// What every page and route is sent with. It says what a page may not be: shown
// inside another site's frame, given a different base address by an injected
// tag, or made to embed a plug-in or send a form to another site. It does not
// limit which sites a page may load from or send to: on a desk the page talks
// to its own backend, at an address the environment sets, and a fixed list
// would have to follow it.
//
// Cross-Origin-Opener-Policy is left alone on purpose: "Open in Hindsight"
// opens another page and has to hear back from it, and that policy would cut
// the tie.
//
// The one site allowed to show the desk inside a frame is Uzair's portfolio,
// where the hosted copy runs in a window. Nobody else may. That is
// `frame-ancestors` below. The older X-Frame-Options header can only say
// "nobody" or "this site", not "this one other site", and browsers that read
// both obey `frame-ancestors`, so it is left out.
const PORTFOLIO = "https://uzair-khan-lac.vercel.app";

const securityHeaders = [
  { key: "Content-Security-Policy", value: `frame-ancestors 'self' ${PORTFOLIO}; base-uri 'self'; object-src 'none'; form-action 'self'` },
  // A file is what its type says it is, and is never guessed to be a script.
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
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
