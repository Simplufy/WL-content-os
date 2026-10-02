import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, type Creator } from "../api";
import { Avatar, Empty, ErrorBox, OutlierPill, PlatformBadge, ScorePill } from "../components/ui";
import { ago, compact, useLoad } from "../util";

export default function Creators() {
  const { data, error, reload } = useLoad(() => api.creators(), [], 5000);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "error" | "warn" | "ok"; text: string } | null>(null);
  const nav = useNavigate();

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const c = await api.addCreator(url.trim());
      setUrl("");
      setMsg(
        c.warning
          ? { kind: "warn", text: c.warning }
          : { kind: "ok", text: `Added @${c.handle}. Pulling their recent posts and analyzing their best ones now.` },
      );
      reload();
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof ApiError ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Creators</h1>
          <p className="sub">Paste a profile link. Studio checks each creator every 3 hours and analyzes every new post.</p>
        </div>
      </div>

      <form className="add-bar" onSubmit={add}>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.tiktok.com/@creator  ·  youtube.com/@creator  ·  instagram.com/creator"
          aria-label="Profile link"
        />
        <button className="btn btn-primary" disabled={busy || !url.trim()}>
          {busy ? "Adding…" : "Add creator"}
        </button>
      </form>
      {msg && <div className={`alert alert-${msg.kind === "ok" ? "ok" : msg.kind}`}>{msg.text}</div>}
      <ErrorBox error={error} />

      {data && data.length === 0 && (
        <Empty title="No creators yet">Add the people whose content you want to learn from. Their best and newest posts get analyzed right away.</Empty>
      )}

      {data && data.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Creator</th>
                <th className="num">Posts</th>
                <th className="num">Median views</th>
                <th className="num">Best outlier</th>
                <th className="num">Avg score</th>
                <th>Last post</th>
                <th>Monitor</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.map((c) => (
                <CreatorRow key={c.id} c={c} onChange={reload} onOpen={() => nav(`/creators/${c.id}`)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CreatorRow({ c, onChange, onOpen }: { c: Creator; onChange: () => void; onOpen: () => void }) {
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChange();
    } finally {
      setBusy(false);
    }
  };
  const checking = c.check_pending;
  return (
    <tr className={c.active ? "" : "row-muted"}>
      <td>
        <Link to={`/creators/${c.id}`} className="creator-cell">
          <Avatar src={c.avatar_url} name={c.handle} />
          <div>
            <div className="strong">{c.display_name || `@${c.handle}`}</div>
            <div className="muted small">
              @{c.handle} <PlatformBadge platform={c.platform} small />
              {c.followers ? <> · {compact(c.followers)} followers</> : null}
            </div>
          </div>
        </Link>
      </td>
      <td className="num" onClick={onOpen}>
        {c.analyzed ?? 0}
        <span className="muted"> / {c.posts ?? 0}</span>
        {c.processing ? <div className="muted small">{c.processing} processing</div> : null}
      </td>
      <td className="num">{compact(c.baseline?.median_views)}</td>
      <td className="num">
        <OutlierPill x={c.best_outlier} />
      </td>
      <td className="num">
        <ScorePill score={c.avg_score} />
      </td>
      <td className="muted">{ago(c.last_post)}</td>
      <td>
        {checking ? (
          <span className="status status-downloading">
            <span className="spinner" /> Checking…
          </span>
        ) : c.last_check_status === "error" ? (
          <span className="status status-failed" title={c.last_check_error || ""}>
            Check failed
          </span>
        ) : c.active ? (
          <span className="muted small">Checked {ago(c.last_checked_at)}</span>
        ) : (
          <span className="muted small">Paused</span>
        )}
        {c.last_check_status === "error" && c.last_check_error && <div className="err-line">{c.last_check_error}</div>}
      </td>
      <td className="actions">
        <button className="btn btn-sm" disabled={busy || checking} onClick={() => run(() => api.checkCreator(c.id))}>
          Check now
        </button>
        <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => run(() => api.patchCreator(c.id, { active: !c.active }))}>
          {c.active ? "Pause" : "Resume"}
        </button>
        <button
          className="btn btn-sm btn-ghost btn-danger"
          disabled={busy}
          onClick={() => confirm(`Remove @${c.handle} and all their analyzed posts?`) && run(() => api.deleteCreator(c.id))}
        >
          Remove
        </button>
      </td>
    </tr>
  );
}
