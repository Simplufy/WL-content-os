import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, type Publication } from "../api";
import { Empty, ErrorBox } from "../components/ui";
import { ago, useLoad } from "../util";
import { ProviderDot } from "./PublishNew";

const STATUS: Record<string, { label: string; cls: string }> = {
  submitting: { label: "Sending to Postiz", cls: "status-analyzing" },
  scheduled: { label: "Scheduled", cls: "status-queued" },
  publishing: { label: "Publishing", cls: "status-analyzing" },
  draft: { label: "Draft in Postiz", cls: "" },
  published: { label: "Published", cls: "status-done" },
  error: { label: "Failed", cls: "status-failed" },
  cancelled: { label: "Cancelled", cls: "" },
};

function dayKey(iso: string | null) {
  if (!iso) return "Unscheduled";
  return new Date(iso).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
}

export default function Publish() {
  const pubs = useLoad(() => api.publications(), [], 15000);
  const status = useLoad(() => api.publishStatus(), []);
  const edits = useLoad(() => api.edits(), []);
  const [msg, setMsg] = useState<string | null>(null);

  if (!pubs.data) return <div className="page">{pubs.error ? <ErrorBox error={pubs.error} /> : <div className="loading">Loading…</div>}</div>;
  const upcoming = pubs.data.filter((p) => ["submitting", "scheduled", "publishing", "draft"].includes(p.status));
  const past = pubs.data.filter((p) => ["published", "error", "cancelled"].includes(p.status));
  const readyEdits = (edits.data || []).filter((e) => e.status === "ready");
  const groups: Record<string, Publication[]> = {};
  for (const p of [...upcoming].sort((a, b) => (a.scheduled_at || a.created_at).localeCompare(b.scheduled_at || b.created_at))) {
    (groups[dayKey(p.scheduled_at)] ||= []).push(p);
  }
  const st = status.data;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Publish</h1>
          <p className="sub">Schedule finished edits to every channel through Postiz. Nothing posts without your confirmation.</p>
        </div>
        <div className="row gap">
          <button
            className="btn"
            onClick={async () => {
              await api.syncPublications();
              setMsg("Checking Postiz for updates…");
              setTimeout(() => pubs.reload(), 4000);
            }}
          >
            Check status
          </button>
          <Link to="/publish/setup" className="btn">
            Channels &amp; setup
          </Link>
        </div>
      </div>
      {msg && <div className="alert alert-info">{msg}</div>}
      {st && st.integrations.length === 0 && (
        <div className="alert alert-warn">
          <strong>No channels connected in Postiz yet.</strong> Scheduling works as soon as they are. <Link to="/publish/setup">What's needed →</Link>
        </div>
      )}

      <div className="two-col">
        <section className="card">
          <div className="card-head">
            <h2>Coming up</h2>
          </div>
          {upcoming.length === 0 ? (
            <Empty title="Nothing scheduled">Open a finished edit and hit Publish.</Empty>
          ) : (
            Object.entries(groups).map(([day, list]) => (
              <div key={day} className="cal-day">
                <div className="cal-date">{day}</div>
                {list.map((p) => (
                  <PubRow key={p.id} p={p} onChange={pubs.reload} onError={setMsg} />
                ))}
              </div>
            ))
          )}
        </section>

        <section className="card">
          <div className="card-head">
            <h2>Ready to publish</h2>
          </div>
          {readyEdits.length === 0 ? (
            <Empty title="No finished edits">Finished edits from the Editor show up here.</Empty>
          ) : (
            <div className="ready-list">
              {readyEdits.map((e) => {
                const n = pubs.data!.filter((p) => p.edit_id === e.id && p.status !== "cancelled").length;
                return (
                  <div key={e.id} className="ready-row">
                    {e.thumb ? <img src={`${e.thumb}?v=${e.render_count}`} alt="" /> : <div className="vtile-noimg" />}
                    <div className="grow">
                      <div className="strong">{e.title}</div>
                      <div className="muted small">{n ? `${n} publication${n > 1 ? "s" : ""}` : "Not published yet"}</div>
                    </div>
                    <Link to={`/publish/new/${e.id}`} className="btn btn-sm btn-primary">
                      Publish
                    </Link>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>

      <section className="section">
        <div className="section-head">
          <h2>History</h2>
        </div>
        {past.length === 0 ? (
          <Empty title="Nothing published yet" />
        ) : (
          <div className="card">
            {past.map((p) => (
              <PubRow key={p.id} p={p} onChange={pubs.reload} onError={setMsg} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function PubRow({ p, onChange, onError }: { p: Publication; onChange: () => void; onError: (m: string) => void }) {
  const s = STATUS[p.status] || { label: p.status, cls: "" };
  const urlFor = (id: string) => p.results.find((r) => r.id === id)?.url;
  return (
    <div className="pub-row">
      {p.thumb ? <img src={p.thumb} alt="" /> : <div className="vtile-noimg" />}
      <div className="grow">
        <div className="row gap wrap">
          <Link to={`/editor/${p.edit_id}`} className="strong">
            {p.title}
          </Link>
          <span className={`status ${s.cls}`}>{s.label}</span>
          {p.stale_render && <span className="tag tag-warn" title="The edit was re-rendered after this was sent">older render</span>}
        </div>
        <div className="muted small">
          {p.scheduled_at ? new Date(p.scheduled_at).toLocaleString([], { hour: "numeric", minute: "2-digit", month: "short", day: "numeric" }) : ago(p.created_at)}
          {" · "}
          {p.channels.map((c, k) => {
            const url = p.results[k] ? urlFor(p.results[k].id) : null;
            return (
              <span key={c.integration_id} className="chan-chip">
                <ProviderDot provider={c.provider} /> {url ? <a href={url} target="_blank" rel="noreferrer">{c.name} ↗</a> : c.name}
              </span>
            );
          })}
        </div>
        {p.error && <div className="err-line">{p.error}</div>}
      </div>
      {["scheduled", "draft"].includes(p.status) && (
        <button
          className="btn btn-sm btn-ghost btn-danger"
          onClick={async () => {
            if (!confirm("Cancel this scheduled post? It's removed from Postiz.")) return;
            try {
              await api.cancelPublication(p.id);
              onChange();
            } catch (e) {
              onError(e instanceof ApiError ? e.message : String(e));
            }
          }}
        >
          Cancel
        </button>
      )}
      {p.status === "error" && !p.results.length && (
        <button className="btn btn-sm" onClick={() => api.retryPublication(p.id).then(onChange)}>
          Retry
        </button>
      )}
    </div>
  );
}
