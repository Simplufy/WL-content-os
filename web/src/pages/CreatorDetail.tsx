import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Avatar, Empty, ErrorBox, Kpi, PlatformBadge, VideoTile } from "../components/ui";
import { ago, compact, fmtX } from "../util";
import { useLoad } from "../util";

export default function CreatorDetail() {
  const id = Number(useParams().id);
  const { data: c, error, reload } = useLoad(() => api.creator(id), [id], 5000);
  const [filter, setFilter] = useState<"all" | "analyzed" | "new">("all");
  if (!c) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="loading">Loading…</div>}</div>;

  const videos = (c.videos || []).filter((v) => (filter === "analyzed" ? v.status === "done" : filter === "new" ? v.is_new : true));
  const analyzed = (c.videos || []).filter((v) => v.status === "done");
  const best = [...(c.videos || [])].sort((a, b) => (b.outlier ?? -1) - (a.outlier ?? -1))[0];

  return (
    <div className="page">
      <Link to="/creators" className="back">
        ← Creators
      </Link>
      <div className="page-head">
        <div className="creator-head">
          <Avatar src={c.avatar_url} name={c.handle} size={56} />
          <div>
            <h1>{c.display_name || `@${c.handle}`}</h1>
            <div className="muted">
              <a href={c.profile_url} target="_blank" rel="noreferrer">
                @{c.handle}
              </a>{" "}
              <PlatformBadge platform={c.platform} /> {c.followers ? `· ${compact(c.followers)} followers` : ""} · checked{" "}
              {ago(c.last_checked_at)}
            </div>
          </div>
        </div>
        <button className="btn" onClick={() => api.checkCreator(c.id).then(reload)}>
          Check now
        </button>
      </div>
      {c.last_check_status === "error" && <div className="alert alert-error">Last check failed: {c.last_check_error}</div>}

      <div className="kpis">
        <Kpi label="Posts tracked" value={c.videos?.length ?? 0} hint={`${analyzed.length} analyzed`} />
        <Kpi label="Median views" value={compact(c.baseline.median_views)} hint={`from ${c.baseline.n} posts`} />
        <Kpi label="Median engagement" value={c.baseline.median_er !== null ? `${c.baseline.median_er.toFixed(1)}%` : "—"} />
        <Kpi label="Best outlier" value={fmtX(best?.outlier)} tone={best?.outlier && best.outlier >= 3 ? "hot" : undefined} />
      </div>

      <div className="tabs">
        {(["all", "analyzed", "new"] as const).map((f) => (
          <button key={f} className={`tab ${filter === f ? "active" : ""}`} onClick={() => setFilter(f)}>
            {f === "all" ? "All posts" : f === "analyzed" ? "Analyzed" : "New since added"}
          </button>
        ))}
      </div>
      {videos.length ? (
        <div className="vgrid">
          {videos.map((v) => (
            <VideoTile key={v.id} v={v} />
          ))}
        </div>
      ) : (
        <Empty title="Nothing here yet" />
      )}
    </div>
  );
}
