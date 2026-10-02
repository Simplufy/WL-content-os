import { Link } from "react-router-dom";
import type { Platform, VideoCard } from "../api";
import { ago, compact, fmtX, HOOK_TYPE_LABEL, mmss, outlierTone, scoreTone, STATUS_LABEL } from "../util";

export function PlatformBadge({ platform, small }: { platform: Platform; small?: boolean }) {
  const label = { tiktok: "TikTok", youtube: "YouTube", instagram: "Instagram" }[platform];
  return <span className={`platform platform-${platform}${small ? " platform-sm" : ""}`}>{label}</span>;
}

export function Avatar({ src, name, size = 36 }: { src?: string | null; name: string; size?: number }) {
  const initials = name.replace(/^@/, "").slice(0, 2).toUpperCase();
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.36 }}>
      {src ? <img src={src} alt="" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.style.display = "none")} /> : null}
      <span>{initials}</span>
    </span>
  );
}

export function ScorePill({ score, label }: { score: number | null | undefined; label?: string }) {
  return (
    <span className={`pill tone-${scoreTone(score)}`} title={label}>
      {score === null || score === undefined ? "—" : Math.round(score)}
    </span>
  );
}

export function OutlierPill({ x }: { x: number | null | undefined }) {
  return (
    <span className={`pill tone-${outlierTone(x)}`} title="Views vs this creator's median">
      {fmtX(x)}
    </span>
  );
}

export function StatusTag({ status, error }: { status: string; error?: string | null }) {
  const busy = ["queued", "downloading", "transcribing", "analyzing"].includes(status);
  return (
    <span className={`status status-${status}`} title={error || undefined}>
      {busy && <span className="spinner" />}
      {STATUS_LABEL[status] || status}
    </span>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {children && <div className="empty-body">{children}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="alert alert-error">{error}</div>;
}

export function Kpi({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: string; tone?: string }) {
  return (
    <div className={`kpi${tone ? ` kpi-${tone}` : ""}`}>
      <div className="kpi-value">{value}</div>
      <div className="kpi-label">{label}</div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  );
}

export function VideoTile({ v }: { v: VideoCard }) {
  return (
    <Link to={`/videos/${v.id}`} className="vtile">
      <div className="vtile-media">
        {v.poster ? <img src={v.poster} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <div className="vtile-noimg" />}
        <div className="vtile-top">
          <OutlierPill x={v.outlier} />
          {v.is_new ? <span className="pill pill-new">NEW</span> : null}
        </div>
        <div className="vtile-bottom">
          <span>{compact(v.views)} views</span>
          {v.duration ? <span>{mmss(v.duration)}</span> : null}
        </div>
        {v.status !== "done" && (
          <div className="vtile-status">
            <StatusTag status={v.status} error={v.error} />
          </div>
        )}
      </div>
      <div className="vtile-body">
        <div className="vtile-hook">{v.hook?.spoken || v.title || "Untitled"}</div>
        <div className="vtile-meta">
          <span className="muted">@{v.handle}</span>
          <span className="muted">·</span>
          <span className="muted">{ago(v.published_at)}</span>
          <span className="spacer" />
          {v.score !== null && <ScorePill score={v.score} label="Overall score" />}
        </div>
        {v.hook?.type && <div className="vtile-tag">{HOOK_TYPE_LABEL[v.hook.type] || v.hook.type}</div>}
      </div>
    </Link>
  );
}

export function Bar({ value, max = 10 }: { value: number; max?: number }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <span className="bar">
      <span className="bar-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}
