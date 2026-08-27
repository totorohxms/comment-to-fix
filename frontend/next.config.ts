import type { NextConfig } from "next";

// The FastAPI backend. Client-side calls go to /api/* on this origin and are
// rewritten; server components call it directly via BACKEND_URL.
const backend = process.env.BACKEND_URL ?? "http://localhost:4173";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
