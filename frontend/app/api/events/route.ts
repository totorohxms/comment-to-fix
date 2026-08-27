// Streaming SSE proxy. The generic /api/* rewrite is fine for JSON, but the
// Next standalone server BUFFERS streamed rewrite responses, which silently
// kills SSE in production (dev streams, prod doesn't — verified on Render).
// A route handler takes precedence over the rewrite and pipes the backend's
// event stream through untouched.

import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:4173";

export async function GET(req: NextRequest) {
  const upstream = await fetch(
    `${BACKEND}/api/events?${req.nextUrl.searchParams.toString()}`,
    {
      headers: { "last-event-id": req.headers.get("last-event-id") ?? "" },
      cache: "no-store",
      signal: req.signal, // drop the backend subscription when the client goes away
    },
  );
  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
