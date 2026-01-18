import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  env: {
    // "1" builds the view-only demo: no backend, every page reads public/showcase/snapshot.json.
    // A hosted copy has no backend to talk to, so on Vercel that is the default.
    // Set NEXT_PUBLIC_SHOWCASE yourself to override it either way.
    NEXT_PUBLIC_SHOWCASE: process.env.NEXT_PUBLIC_SHOWCASE ?? (process.env.VERCEL ? "1" : "0"),
  },
};

export default nextConfig;
