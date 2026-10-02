import { useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import { StyleThumb } from "../components/StylePicker";
import { ErrorBox } from "../components/ui";
import { ago, useLoad } from "../util";

const KNOB_LABEL: Record<string, string> = {
  caption_position: "Captions", caption_words: "Words on screen", caption_size: "Caption size", caption_case: "Case",
  caption_font: "Caption font", pace: "Pace", zoom_mode: "Zooms", zoom_strength: "Zoom strength",
  graphics_density: "Graphics", hook_title: "Hook card", color_grade: "Grade", sfx: "Sound design",
};

export default function Styles() {
  const { data, error, reload } = useLoad(() => api.styles(), [], 6000);
  const [msg, setMsg] = useState<string | null>(null);
  if (!data) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="loading">Loading…</div>}</div>;
  const st = data.status;
  const working = ["queued", "fingerprinting", "synthesizing"].includes(st.state);

  return (
    <div className="page wide">
      <div className="page-head">
        <div>
          <h1>Editing styles</h1>
          <p className="sub">
            Learned from what the best videos in your library do — cut pace, captions, zooms, graphics, grade — then rebuilt in your brand.
            Pick one when you upload or on any edit.
          </p>
        </div>
        <button
          className="btn btn-primary"
          disabled={working}
          onClick={async () => {
            setMsg(null);
            try {
              await api.buildStyles();
              reload();
            } catch (e) {
              setMsg(e instanceof ApiError ? e.message : String(e));
            }
          }}
        >
          {working ? "Building…" : data.items.some((s) => s.source === "library") ? "Evolve now" : "Build styles from library"}
        </button>
      </div>
      {msg && <div className="alert alert-error">{msg}</div>}
      {working && (
        <div className="alert alert-info">
          <span className="spinner" />
          {st.state === "fingerprinting"
            ? `Reading the editing style of your best videos — ${data.fingerprinted} done${st.remaining ? `, ${st.remaining} to go` : ""}. About a minute each.`
            : st.state === "synthesizing"
              ? "Turning the fingerprints into styles…"
              : "Starting…"}
        </div>
      )}
      {st.state === "failed" && <div className="alert alert-error">Style build failed: {st.error}</div>}
      <p className="muted small">
        {data.fingerprinted} of {data.analyzed} analyzed videos fingerprinted
        {data.last_build ? ` · styles last evolved ${ago(data.last_build.at)}` : ""}
        {data.auto && data.last_build ? ` · next evolve after ${Math.max(0, 10 - (data.fingerprinted - data.last_build.fingerprints))} more new videos` : ""}
      </p>
      {data.last_build?.changelog && data.last_build.changelog !== "first build" && (
        <div className="alert alert-info">
          Last evolution: {data.last_build.added ?? 0} new, {data.last_build.updated ?? 0} refined, {data.last_build.retired ?? 0} retired — {data.last_build.changelog}
        </div>
      )}

      <div className="style-grid">
        {data.items.map((s) => (
          <div key={s.id} className="style-card">
            <StyleThumb s={s} size={200} />
            <div className="style-body">
              <div className="row gap wrap">
                <h2>{s.name}</h2>
                {s.source === "builtin" && <span className="tag">house style</span>}
              </div>
              <p>{s.description}</p>
              {s.best_for && <p className="muted small">Best for: {s.best_for}</p>}
              <div className="chips">
                {Object.entries(s.params).map(([k, v]) => (
                  <span key={k} className="chip" title={KNOB_LABEL[k] || k}>
                    {KNOB_LABEL[k] || k}: <strong>{String(v)}</strong>
                  </span>
                ))}
              </div>
              {s.inspired.length > 0 && (
                <div className="inspired-row">
                  <span className="muted small">Learned from</span>
                  {s.inspired.slice(0, 5).map((v) => (
                    <Link key={v.id} to={`/videos/${v.id}`} title={`@${v.handle}`}>
                      {v.poster ? <img src={v.poster} alt="" referrerPolicy="no-referrer" /> : <span className="vtile-noimg" />}
                    </Link>
                  ))}
                </div>
              )}
              <div className="row gap mt">
                <button className="btn btn-sm" onClick={() => api.rerenderStylePreview(s.id).then(reload)}>
                  Re-render preview
                </button>
                {s.source !== "builtin" && (
                  <button className="btn btn-sm btn-ghost btn-danger" onClick={() => confirm(`Remove “${s.name}”?`) && api.patchStyle(s.id, { archived: true }).then(reload)}>
                    Remove
                  </button>
                )}
              </div>
              {s.preview_error && <div className="err-line">{s.preview_error}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
