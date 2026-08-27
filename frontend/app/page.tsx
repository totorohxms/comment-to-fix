import Link from "next/link";

export default function Landing() {
  return (
    <div className="landing">
      <div className="landing-wrap">
        <h1>💬🔧 <span>commentToFix</span></h1>
        <p>
          Comment on a live site like a Google Doc — an agent captures the runtime
          context, ships a fix to a preview deployment, and you iterate in the same
          thread until the PR merges.
        </p>
        <ol>
          <li>Open the demo site and hit <b>💬 Comment</b>, then click any element.</li>
          <li>Try: <code>@agent this button style is not right</code> on the yellow Edit Profile button — <code>@agent</code> launches the fix; comments without it are plain discussion.</li>
          <li>Try: <code>@agent this button should not show up</code> on the red Export Data button.</li>
          <li>Watch the thread move: triggered → analyzing → coding → deploying → preview ready.</li>
          <li>Open the preview, keep iterating (<code>@agent make it green</code>), then switch to <b>Evan (Engineer)</b> to approve — only the approver group can open a PR.</li>
          <li>Chaos words for failure demos: <code>flaky</code> (retry), <code>fatal</code> (dead-letter queue), <code>vanish</code> (janitor reclaims a lost worker).</li>
          <li>Switch to <b>Vic (Viewer)</b> for view-only; click the 📦 chip on any comment to inspect the capture bundle.</li>
        </ol>
        <Link className="cta" href="/demo/profile">Open the demo site →</Link>
      </div>
    </div>
  );
}
