import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorBox, Kpi, OutlierPill, ScorePill, StatusTag, VideoTile } from "../components/ui";
import { ago, compact, HOOK_TYPE_LABEL, STATUS_LABEL, useLoad } from "../util";

export default function DashboardPage() {
  const { data, error } = useLoad(() => api.dashboard(), [], 6000);
  if (!data) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="loading">Loading…</div>}</div>;
  const k = data.kpis;
  const noCreators = k.creators === 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="sub">What's working in your niche right now, and what's new since you last looked.</p>
        </div>
        <Link to="/creators" className="btn btn-primary">
          + Add creator
        </Link>
      </div>

      {data.llm.ok === false && (
        <div className="alert alert-warn">
          <strong>Claude isn't connected.</strong> Videos are still downloaded and transcribed, but teardowns and hook
          scores wait until it is. <Link to="/settings">Fix in Settings →</Link>
        </div>
      )}

      {noCreators ? (
        <Empty title="Start by adding the creators you want to learn from">
          Paste a TikTok, YouTube or Instagram profile link on the <Link to="/creators">Creators</Link> page. Studio checks
          them every few hours, downloads each new post, transcribes it, tears down the hook and scores it against that
          creator's normal performance.
        </Empty>
      ) : (
        <>
          <div className="kpis">
            <Kpi label="Creators watched" value={k.creators} />
            <Kpi label="Posts analyzed" value={compact(k.videos_analyzed)} hint={`${compact(k.videos_tracked)} tracked`} />
            <Kpi label="New posts this week" value={k.new_this_week} />
            <Kpi label="Ideas / scripts ready" value={`${k.ideas_open} / ${k.scripts_ready}`} />
            <Kpi label="Scheduled posts" value={k.scheduled ?? 0} hint={k.published_30d ? `${k.published_30d} published (30d)` : undefined} />
            <Kpi label="3×+ outliers (30d)" value={k.outliers_this_month} tone={k.outliers_this_month ? "hot" : undefined} />
            <Kpi
              label="Processing"
              value={k.in_queue}
              hint={k.waiting_llm ? `${k.waiting_llm} waiting for Claude` : k.failed ? `${k.failed} failed` : undefined}
            />
          </div>

          {data.processing.length > 0 && (
            <div className="card processing">
              <div className="card-head">
                <h2>In progress</h2>
              </div>
              <div className="proc-list">
                {data.processing.map((p) => (
                  <Link key={p.id} to={`/videos/${p.id}`} className="proc-row">
                    <StatusTag status={p.status} />
                    <span className="muted">@{p.handle}</span>
                    <span className="ellipsis">{p.title || STATUS_LABEL[p.status]}</span>
                  </Link>
                ))}
              </div>
            </div>
          )}

          <section className="section">
            <div className="section-head">
              <h2>Top outliers</h2>
              <span className="muted">Posts beating their creator's median views, last 60 days</span>
              <Link to="/videos?sort=outlier" className="link-more">
                See all →
              </Link>
            </div>
            {data.outliers.length ? (
              <div className="vgrid">
                {data.outliers.map((v) => (
                  <VideoTile key={v.id} v={v} />
                ))}
              </div>
            ) : (
              <Empty title="No scored posts yet">Outliers appear once a creator has at least 3 posts with view counts.</Empty>
            )}
          </section>

          <div className="two-col">
            <section className="card">
              <div className="card-head">
                <h2>Best hooks</h2>
                <Link to="/hooks" className="link-more">
                  Hook library →
                </Link>
              </div>
              {data.top_hooks.length ? (
                <div className="hook-list">
                  {data.top_hooks.map((v) => (
                    <Link key={v.id} to={`/videos/${v.id}`} className="hook-row">
                      <ScorePill score={v.score} />
                      <div className="hook-row-body">
                        <div className="hook-quote">“{v.hook?.spoken || v.title}”</div>
                        <div className="muted small">
                          @{v.handle} · {v.hook?.type ? HOOK_TYPE_LABEL[v.hook.type] : ""} · {compact(v.views)} views
                        </div>
                      </div>
                      <OutlierPill x={v.outlier} />
                    </Link>
                  ))}
                </div>
              ) : (
                <Empty title="No hooks analyzed yet">
                  {data.llm.ok === false ? "Waiting for Claude to be connected." : "They'll show up as posts finish analysis."}
                </Empty>
              )}
            </section>

            <section className="card">
              <div className="card-head">
                <h2>Activity</h2>
              </div>
              {data.events.length ? (
                <ul className="events">
                  {data.events.map((e) => (
                    <li key={e.id} className={`event event-${e.level}`}>
                      <span className="event-dot" />
                      <span className="event-msg">
                        {e.video_id ? <Link to={`/videos/${e.video_id}`}>{e.message}</Link> : e.creator_id ? <Link to={`/creators/${e.creator_id}`}>{e.message}</Link> : e.message}
                      </span>
                      <span className="muted small">{ago(e.at)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty title="Nothing yet" />
              )}
            </section>
          </div>

          <section className="section">
            <div className="section-head">
              <h2>Latest posts</h2>
              <Link to="/videos?sort=recent" className="link-more">
                All videos →
              </Link>
            </div>
            <div className="vgrid">
              {data.newest.map((v) => (
                <VideoTile key={v.id} v={v} />
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  );
}
