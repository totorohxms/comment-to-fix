// The thread's LIVING preview link: one stable URL per thread that always
// renders the current tip. Iterate again and this same link shows the new
// version on refresh — /preview/[sha] permalinks stay frozen as history.

import { notFound } from "next/navigation";
import { ProfileSite } from "@/components/demo/ProfileSite";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:4173";
export const dynamic = "force-dynamic";

export default async function LivePreviewPage(
  { params }: { params: Promise<{ threadId: string }> },
) {
  const { threadId } = await params;
  if (!/^[\w-]{4,40}$/.test(threadId)) notFound();
  const res = await fetch(`${BACKEND}/api/previews/live/${threadId}`, { cache: "no-store" });
  if (!res.ok) notFound();
  const preview = await res.json();
  return <ProfileSite sha={preview.sha} env="preview" patchesCss={preview.css} />;
}
