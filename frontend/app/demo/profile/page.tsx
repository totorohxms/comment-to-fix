// The "production" demo site: rendered at the current main deployment sha.

import { ProfileSite } from "@/components/demo/ProfileSite";

const BACKEND = process.env.BACKEND_URL ?? "http://localhost:4173";
export const dynamic = "force-dynamic";

export default async function ProfilePage() {
  const meta = await fetch(`${BACKEND}/api/meta`, { cache: "no-store" }).then((r) => r.json());
  return <ProfileSite sha={meta.mainSha} env="production" patchesCss="" />;
}
