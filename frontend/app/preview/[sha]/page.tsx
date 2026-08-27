// A preview deployment: the same site with that sha's accumulated agent
// patches applied (in real life this is a per-branch deploy at
// preview-<sha>.example.com).

import { notFound } from "next/navigation";
import { ProfileSite } from "@/components/demo/ProfileSite";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:4173";
export const dynamic = "force-dynamic";

export default async function PreviewPage({ params }: { params: Promise<{ sha: string }> }) {
  const { sha } = await params;
  if (!/^[0-9a-f]{4,16}$/.test(sha)) notFound();
  const res = await fetch(`${BACKEND}/api/previews/${sha}`, { cache: "no-store" });
  if (!res.ok) notFound();
  const preview = await res.json();
  return <ProfileSite sha={sha} env="preview" patchesCss={preview.css} />;
}
