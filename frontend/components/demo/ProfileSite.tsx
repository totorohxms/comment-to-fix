"use client";

// DEMO-ONLY: the fake "Acme Social" profile page the widget is demoed on.
// Element ids (#btn-edit, #btn-export, …) are the selectors agent patches
// target — keep them stable. Two bugs are planted on purpose:
//   #btn-edit   ugly style   (case 1: designer comment)
//   #btn-export leaked internal tool (case 2: engineer comment)

import { useEffect, useState } from "react";
import { Widget } from "@/components/widget/Widget";

interface Profile { followers: number; following: number; projects: number }
interface Activity { t: string; at: string }

function ProfileHeader() {
  return (
    <section className="card head-card">
      <div className="avatar">MC</div>
      <div className="head-info">
        <h1 id="profile-name">Maya Chen</h1>
        <p className="role">Staff Product Designer · San Francisco</p>
        <p className="bio">Designing calm software. Previously @ Figma, @ Linear. Coffee, ceramics, and design tokens.</p>
      </div>
      <div className="head-actions">
        <button id="btn-follow" className="btn btn-follow" onClick={() => console.log("followed")}>Follow</button>
        <button id="btn-message" className="btn btn-message" onClick={() => console.log("open message composer")}>Message</button>
        <button id="btn-edit" className="btn btn-edit" onClick={() => console.log("open edit profile")}>Edit Profile</button>
        <button id="btn-export" className="btn btn-export"
                onClick={() => console.warn("export-data: internal tool invoked from public profile!")}>
          Export Data
        </button>
      </div>
    </section>
  );
}

function ProfileStats({ profile }: { profile: Profile | null }) {
  return (
    <section className="stats card">
      <div className="stat"><b id="stat-followers">{profile?.followers.toLocaleString() ?? "—"}</b><span>Followers</span></div>
      <div className="stat"><b id="stat-following">{profile?.following ?? "—"}</b><span>Following</span></div>
      <div className="stat"><b id="stat-projects">{profile?.projects ?? "—"}</b><span>Projects</span></div>
    </section>
  );
}

function ActivityList({ activity }: { activity: Activity[] }) {
  return (
    <section className="card">
      <h2>Recent activity</h2>
      <ul id="activity" className="activity">
        {activity.length === 0 && <li className="muted">loading…</li>}
        {activity.map((a) => <li key={a.t}>{a.t}<small>{a.at}</small></li>)}
      </ul>
    </section>
  );
}

function PinnedProjects() {
  return (
    <section className="card">
      <h2>Pinned projects</h2>
      <div className="project"><b>design-tokens</b><p>Single source of truth for Acme&apos;s colors, spacing, type.</p><span className="tag">Figma → CSS</span></div>
      <div className="project"><b>dark-mode-v2</b><p>System-aware theming across web + mobile.</p><span className="tag">shipped</span></div>
    </section>
  );
}

export function ProfileSite({ sha, env, patchesCss }: {
  sha: string;
  env: "production" | "preview";
  patchesCss: string;
}) {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);

  // Fake app traffic — recorded by the widget's network capture buffer.
  useEffect(() => {
    (async () => {
      try {
        const p = await (await fetch("/api/demo/profile")).json();
        setProfile(p);
        console.log("profile loaded", p.id);
        setActivity(await (await fetch("/api/demo/activity")).json());
        const flags = await (await fetch("/api/demo/flags")).json();
        console.log("feature flags", flags);
        // Bug (demo case 2): exportData flag should gate an internal-only
        // tool, but the check was dropped — the button always renders.
      } catch (e) {
        console.error("profile page load failed", e);
      }
    })();
  }, []);

  return (
    <>
      {/* Preview deployments: the accumulated agent patches for this sha */}
      {patchesCss && <style dangerouslySetInnerHTML={{ __html: patchesCss }} />}
      <nav className="topnav">
        <span className="brand">🌐 Acme Social</span>
        <input className="search" placeholder="Search people, projects…" />
        <span className="nav-user">🧑‍💻 you</span>
      </nav>
      <main className="profile">
        <div className="cover" />
        <ProfileHeader />
        <ProfileStats profile={profile} />
        <div className="cols">
          <ActivityList activity={activity} />
          <PinnedProjects />
        </div>
      </main>
      <Widget sha={sha} env={env} />
    </>
  );
}
