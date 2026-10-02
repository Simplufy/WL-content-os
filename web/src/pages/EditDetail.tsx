import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type EditOptions, type EditProject } from "../api";
import StylePicker from "../components/StylePicker";
import { ErrorBox } from "../components/ui";
import { mmss, useLoad } from "../util";
import { EditStatus } from "./Editor";

type Word = NonNullable<EditProject["words"]>[number];
type Run = { kept: boolean; words: Word[]; reason: string | null };

function runsOf(words: Word[]): Run[] {
  const runs: Run[] = [];
  for (const w of words) {
    const last = runs[runs.length - 1];
    if (last && last.kept === w.k && (w.k || last.reason === w.r)) last.words.push(w);
    else runs.push({ kept: w.k, words: [w], reason: w.k ? null : w.r });
  }
  return runs;
}

export default function EditDetail() {
  const id = Number(useParams().id);
  const nav = useNavigate();
  const { data: p, error, reload, setData } = useLoad(() => api.edit(id), [id], 4000);
  const styleList = useLoad(() => api.styles(), []);
  const [instr, setInstr] = useState("");
  const [dirty, setDirty] = useState(false);
  const runs = useMemo(() => runsOf(p?.words || []), [p?.words]);

  if (!p) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="loading">Loading…</div>}</div>;
  const busy = ["queued", "preparing", "deciding", "rendering"].includes(p.status);
  const ov = p.overrides || { restore: [], remove: [] };

  const toggleRun = async (r: Run) => {
    const ids = r.words.map((w) => w.i);
    const restore = new Set(ov.restore);
    const remove = new Set(ov.remove);
    for (const i of ids) {
      if (r.kept) {
        restore.delete(i);
        remove.add(i);
      } else {
        remove.delete(i);
        restore.add(i);
      }
    }
    setData(await api.setOverrides(p.id, { restore: [...restore], remove: [...remove] }));
    setDirty(true);
  };
  const setOpt = async (o: Partial<EditOptions>) => {
    setData(await api.patchEdit(p.id, { options: o }));
    setDirty(true);
  };
  const rerender = async () => {
    await api.renderEdit(p.id);
    setDirty(false);
    reload();
  };
  const keptSeconds = (p.words || []).filter((w) => w.k).reduce((a, w) => a + (w.e - w.s), 0);

  return (
    <div className="page">
      <Link to="/editor" className="back">
        ← Editor
      </Link>
      <div className="page-head">
        <div>
          <h1
            className="editable"
            contentEditable
            suppressContentEditableWarning
            onBlur={async (e) => {
              const t = e.currentTarget.textContent?.trim();
              if (t && t !== p.title) setData(await api.patchEdit(p.id, { title: t }));
            }}
          >
            {p.title}
          </h1>
          <div className="row gap wrap muted">
            <EditStatus p={p} />
            {p.raw_duration ? <span>{mmss(p.raw_duration)} raw</span> : null}
            {p.duration ? <span>→ {mmss(p.duration)} final</span> : null}
            {p.segments ? <span>· {p.segments} cuts</span> : null}
            {p.probe?.hdr ? <span>· HDR → SDR</span> : null}
          </div>
        </div>
        <div className="row gap wrap">
          {p.has_output && p.status === "ready" && (
            <Link className="btn btn-primary" to={`/publish/new/${p.id}`}>
              Publish…
            </Link>
          )}
          {p.has_output && (
            <a className="btn" href={`/api/edits/${p.id}/download`}>
              Download MP4
            </a>
          )}
          <button
            className="btn btn-ghost btn-danger"
            onClick={async () => {
              if (confirm("Delete this edit and its files?")) {
                await api.deleteEdit(p.id);
                nav("/editor");
              }
            }}
          >
            Delete
          </button>
        </div>
      </div>
      {p.status === "failed" && <div className="alert alert-error">{p.error}</div>}
      {p.status === "waiting_llm" && <div className="alert alert-warn">{p.error}</div>}

      <div className="detail">
        <div className="detail-media">
          {p.has_output ? (
            <video key={p.render_count} src={`/api/edits/${p.id}/video?v=${p.render_count}`} controls playsInline poster={p.thumb ? `${p.thumb}?v=${p.render_count}` : undefined} />
          ) : (
            <div className="render-wait">
              <div className="spinner big" />
              <div>{p.stage || "Working…"}</div>
              {p.progress ? (
                <div className="upbar">
                  <span style={{ width: `${Math.round(p.progress * 100)}%` }} />
                </div>
              ) : null}
            </div>
          )}
          {dirty && !busy && (
            <button className="btn btn-primary block" onClick={rerender}>
              Re-render with changes
            </button>
          )}
          {busy && p.has_output && <div className="muted small center">Re-rendering… the new cut replaces this when done.</div>}
        </div>

        <div className="detail-main">
          {styleList.data && styleList.data.items.length > 0 && (
            <section className="card">
              <div className="card-head">
                <h2>Style</h2>
                <span className="muted small">{styleList.data.items.find((s) => s.id === p.options.style_id)?.description || "Pick a style, then re-render"}</span>
              </div>
              <StylePicker
                styles={styleList.data.items}
                value={p.options.style_id}
                onPick={async (sid) => {
                  setData(await api.setEditStyle(p.id, sid));
                  setDirty(true);
                }}
              />
            </section>
          )}

          <section className="card">
            <div className="card-head">
              <h2>Look</h2>
              <span className="muted small">fine-tune on top of the style</span>
            </div>
            <div className="opts">
              <label className="check">
                <input type="checkbox" checked={p.options.captions} onChange={(e) => setOpt({ captions: e.target.checked })} /> Captions
              </label>
              <select value={p.options.caption_style} onChange={(e) => setOpt({ caption_style: e.target.value as EditOptions["caption_style"] })} disabled={!p.options.captions}>
                <option value="brand">Brand (Space Grotesk)</option>
                <option value="bold">Bold (Barlow Black)</option>
                <option value="impact">Impact (Anton)</option>
                <option value="clean">Clean (sentence case)</option>
              </select>
              <label className="check">
                <input type="checkbox" checked={p.options.graphics} onChange={(e) => setOpt({ graphics: e.target.checked })} /> Animated graphics
              </label>
              <label className="check">
                <input type="checkbox" checked={p.options.callouts} onChange={(e) => setOpt({ callouts: e.target.checked })} /> Text callouts
              </label>
              <label className="check">
                <input type="checkbox" checked={p.options.hook_title} onChange={(e) => setOpt({ hook_title: e.target.checked })} /> Hook title
              </label>
              <label className="check">
                <input type="checkbox" checked={p.options.zooms} onChange={(e) => setOpt({ zooms: e.target.checked })} /> Punch-in zooms
              </label>
              <label className="check" title="Pauses longer than this are shortened">
                Pace
                <select value={p.options.max_pause} onChange={(e) => setOpt({ max_pause: Number(e.target.value) })}>
                  <option value={0.2}>Fast</option>
                  <option value={0.26}>Normal</option>
                  <option value={0.5}>Relaxed</option>
                </select>
              </label>
            </div>
            {p.hook_text && (
              <div className="muted small mt">
                Hook title: <strong>{p.hook_text}</strong>
              </div>
            )}
          </section>

          {p.notes && (
            <section className="card accent">
              <div className="card-head">
                <h2>Editor's notes</h2>
              </div>
              <p className="pre">{p.notes}</p>
            </section>
          )}

          <section className="card">
            <div className="card-head">
              <h2>Cut</h2>
              <span className="muted small">
                Kept ≈ {mmss(keptSeconds)} of speech · click a <span className="cut-sample">struck</span> section to bring it back, or kept text to remove it
              </span>
            </div>
            {p.reordered && <div className="muted small">Note: a line was moved earlier for a cold open.</div>}
            {runs.length ? (
              <div className="cutlist">
                {runs.map((r, k) => (
                  <span
                    key={k}
                    className={`run ${r.kept ? "run-kept" : "run-cut"}`}
                    title={r.kept ? "Click to cut" : r.reason ? `Cut: ${r.reason} — click to restore` : "Click to restore"}
                    onClick={() => !busy && toggleRun(r)}
                  >
                    {r.words.map((w) => w.w).join(" ")}{" "}
                  </span>
                ))}
              </div>
            ) : (
              <div className="muted">The transcript and cut appear once Claude has made its edit.</div>
            )}
          </section>

          <section className="card">
            <div className="card-head">
              <h2>Re-edit</h2>
            </div>
            <form
              className="instr-row"
              onSubmit={async (e) => {
                e.preventDefault();
                await api.redecideEdit(p.id, instr || undefined);
                setInstr("");
                setDirty(false);
                reload();
              }}
            >
              <input
                value={instr}
                onChange={(e) => setInstr(e.target.value)}
                placeholder="Tell the editor what to change — “under 45s”, “open on the oil light”, “keep the recap”"
                disabled={busy}
              />
              <button className="btn" disabled={busy}>
                Re-edit
              </button>
            </form>
            {p.instruction && <div className="muted small mt">Last direction: “{p.instruction}”</div>}
          </section>
        </div>
      </div>
    </div>
  );
}
