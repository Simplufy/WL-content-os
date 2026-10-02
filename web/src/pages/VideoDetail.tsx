import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Bar, ErrorBox, OutlierPill, PlatformBadge, ScorePill, StatusTag } from "../components/ui";
import { useBrand } from "../brand";
import { ago, compact, fmtX, HOOK_TYPE_LABEL, mmss, useLoad } from "../util";

const SUBSCORE_LABEL: Record<string, string> = {
  curiosity: "Curiosity",
  clarity: "Clarity",
  specificity: "Specificity",
  pattern_interrupt: "Pattern interrupt",
  audience_callout: "Audience callout",
};

export default function VideoDetail() {
  const id = Number(useParams().id);
  const busy = (s?: string) => !!s && ["queued", "downloading", "transcribing", "analyzing"].includes(s);
  const { data: v, error, reload } = useLoad(() => api.video(id), [id], 4000);
  const player = useRef<HTMLVideoElement>(null);
  const [copied, setCopied] = useState(false);
  const brandName = useBrand().org_name;
  const [ideaBusy, setIdeaBusy] = useState(false);
  const nav = useNavigate();

  if (!v) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="loading">Loading…</div>}</div>;
  const a = v.analysis;
  const seek = (t: number) => {
    if (player.current) {
      player.current.currentTime = t;
      player.current.play().catch(() => {});
    }
  };

  return (
    <div className="page">
      <Link to={`/creators/${v.creator_id}`} className="back">
        ← @{v.handle}
      </Link>
      <div className="page-head">
        <div>
          <h1 className="title-clamp">{a?.hook.spoken ? `“${a.hook.spoken}”` : v.title || "Untitled"}</h1>
          <div className="muted">
            <PlatformBadge platform={v.platform} /> @{v.handle} · posted {ago(v.published_at)} · {mmss(v.duration)} ·{" "}
            <a href={v.url} target="_blank" rel="noreferrer">
              Open original ↗
            </a>
          </div>
        </div>
        <div className="row gap">
          <StatusTag status={v.status} error={v.error} />
          {v.status === "done" && (
            <button
              className="btn btn-primary"
              disabled={ideaBusy}
              onClick={async () => {
                setIdeaBusy(true);
                try {
                  await api.generateIdeas({ count: 3, video_ids: [v.id] });
                  nav("/ideas");
                } finally {
                  setIdeaBusy(false);
                }
              }}
            >
              Make 3 ideas from this
            </button>
          )}
          {v.status === "listed" || v.status === "failed" ? (
            <button className="btn btn-primary" onClick={() => api.processVideo(v.id).then(reload)}>
              Analyze this post
            </button>
          ) : (
            <button className="btn" disabled={busy(v.status)} onClick={() => api.analyzeVideo(v.id).then(reload)}>
              Re-run teardown
            </button>
          )}
        </div>
      </div>
      {v.error && v.status !== "done" && <div className={`alert ${v.status === "waiting_llm" ? "alert-warn" : "alert-error"}`}>{v.error}</div>}

      <div className="detail">
        <div className="detail-media">
          {v.has_media ? (
            <video ref={player} src={`/api/media/video/${v.id}`} controls playsInline poster={v.poster || undefined} />
          ) : v.poster ? (
            <img src={v.poster} alt="" referrerPolicy="no-referrer" />
          ) : (
            <div className="vtile-noimg tall" />
          )}
          {v.frames.length > 0 && (
            <div className="frames">
              {v.frames.map((f) => (
                <button key={f.url} className={`frame ${f.hook ? "frame-hook" : ""}`} onClick={() => seek(f.t)} title={`${f.t}s`}>
                  <img src={f.url} alt="" loading="lazy" />
                  <span>{f.t}s</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="detail-main">
          <div className="score-row">
            <div className="score-card">
              <div className="score-big">
                <ScorePill score={v.score} />
              </div>
              <div className="score-label">Overall score</div>
            </div>
            <div className="score-card">
              <div className="score-big">
                <OutlierPill x={v.outlier} />
              </div>
              <div className="score-label">
                vs median {compact(v.baseline.median_views)} views{v.early ? " · early" : ""}
              </div>
            </div>
            <div className="score-card">
              <div className="score-big">{v.engagement_rate !== null ? `${v.engagement_rate.toFixed(1)}%` : "—"}</div>
              <div className="score-label">Engagement {v.engagement_rel !== null ? `(${fmtX(v.engagement_rel)} their norm)` : ""}</div>
            </div>
            <div className="score-card">
              <div className="score-big">
                <ScorePill score={v.hook_score} />
              </div>
              <div className="score-label">Hook score</div>
            </div>
          </div>
          <div className="metrics-line muted">
            {compact(v.views)} views · {compact(v.likes)} likes · {compact(v.comments)} comments · {compact(v.shares)} shares
            {v.saves !== null ? ` · ${compact(v.saves)} saves` : ""}
          </div>

          {a ? (
            <>
              <section className="card">
                <div className="card-head">
                  <h2>The hook</h2>
                  <span className="tag">{HOOK_TYPE_LABEL[a.hook.type || ""] || a.hook.type}</span>
                </div>
                <dl className="hook-dl">
                  <dt>Said</dt>
                  <dd>“{a.hook.spoken}”</dd>
                  {a.hook.onscreen_text && (
                    <>
                      <dt>On screen</dt>
                      <dd>{a.hook.onscreen_text}</dd>
                    </>
                  )}
                  <dt>Visual</dt>
                  <dd>{a.hook.visual}</dd>
                  <dt>Why</dt>
                  <dd>{a.hook.why}</dd>
                  <dt>Template</dt>
                  <dd>
                    <span className="template">{a.hook.template}</span>{" "}
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => {
                        navigator.clipboard.writeText(a.hook.template || "");
                        setCopied(true);
                        setTimeout(() => setCopied(false), 1500);
                      }}
                    >
                      {copied ? "Copied" : "Copy"}
                    </button>
                  </dd>
                </dl>
                <div className="subscores">
                  {Object.entries(a.hook.subscores || {}).map(([k, val]) => (
                    <div key={k} className="subscore">
                      <span>{SUBSCORE_LABEL[k] || k}</span>
                      <Bar value={val} />
                      <span className="num">{val}</span>
                    </div>
                  ))}
                </div>
              </section>

              <section className="card accent">
                <div className="card-head">
                  <h2>For {brandName}</h2>
                  <span className="muted small">Relevance {a.relevance}/10</span>
                </div>
                <p>{a.brand_angle}</p>
              </section>

              <section className="card">
                <div className="card-head">
                  <h2>Breakdown</h2>
                  <span className="muted small">{a.format}</span>
                </div>
                <p>{a.summary}</p>
                <dl className="hook-dl">
                  <dt>Topic</dt>
                  <dd>{a.topic}</dd>
                  <dt>Angle</dt>
                  <dd>{a.angle}</dd>
                  {a.cta && (
                    <>
                      <dt>CTA</dt>
                      <dd>{a.cta}</dd>
                    </>
                  )}
                </dl>
                {a.beats.length > 0 && (
                  <ol className="beats">
                    {a.beats.map((b, i) => (
                      <li key={i}>
                        <button className="ts" onClick={() => seek(b.start)}>
                          {mmss(b.start)}
                        </button>
                        <strong>{b.label}</strong> — {b.summary}
                      </li>
                    ))}
                  </ol>
                )}
                {a.retention_tactics.length > 0 && (
                  <div className="chips">
                    {a.retention_tactics.map((t) => (
                      <span key={t} className="chip">
                        {t}
                      </span>
                    ))}
                  </div>
                )}
              </section>
            </>
          ) : (
            <section className="card">
              <div className="muted">
                {v.status === "listed"
                  ? "This post hasn't been analyzed. Studio analyzes new posts automatically plus each creator's best posts when they're added."
                  : v.status === "waiting_llm"
                    ? "Transcript is ready. The teardown runs as soon as Claude is connected."
                    : "The teardown will appear here when processing finishes."}
              </div>
            </section>
          )}

          {v.transcript.length > 0 && (
            <section className="card">
              <div className="card-head">
                <h2>Transcript</h2>
              </div>
              <div className="transcript">
                {v.transcript.map((s, i) => (
                  <div key={i} className={`tline ${s.start < 3.2 ? "tline-hook" : ""}`}>
                    <button className="ts" onClick={() => seek(s.start)}>
                      {mmss(s.start)}
                    </button>
                    <span>{s.text}</span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {v.snapshots.length > 1 && (
            <section className="card">
              <div className="card-head">
                <h2>Views over time</h2>
              </div>
              <table className="table compact">
                <tbody>
                  {v.snapshots.map((s) => (
                    <tr key={s.at}>
                      <td className="muted">{new Date(s.at).toLocaleString()}</td>
                      <td className="num">{compact(s.views)} views</td>
                      <td className="num">{compact(s.likes)} likes</td>
                      <td className="num">{compact(s.comments)} comments</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
