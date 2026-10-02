import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, type EditProject } from "../api";
import StylePicker from "../components/StylePicker";
import { Empty, ErrorBox } from "../components/ui";
import { ago, mmss, useLoad } from "../util";

export const EDIT_STATUS: Record<string, string> = {
  queued: "Queued",
  preparing: "Preparing footage",
  deciding: "Choosing takes",
  rendering: "Rendering",
  ready: "Ready",
  failed: "Failed",
  waiting_llm: "Waiting for Claude",
};

export function EditStatus({ p }: { p: EditProject }) {
  const busy = ["queued", "preparing", "deciding", "rendering"].includes(p.status);
  const cls = p.status === "ready" ? "status-done" : p.status === "failed" ? "status-failed" : p.status === "waiting_llm" ? "status-waiting_llm" : "status-analyzing";
  return (
    <span className={`status ${cls}`} title={p.error || undefined}>
      {busy && <span className="spinner" />}
      {p.stage && busy ? p.stage : EDIT_STATUS[p.status]}
      {busy && p.progress ? ` ${Math.round(p.progress * 100)}%` : ""}
    </span>
  );
}

export default function Editor() {
  const { data, error } = useLoad(() => api.edits(), [], 4000);
  const ideas = useLoad(() => api.ideas(), []);
  const styleList = useLoad(() => api.styles(), []);
  const [styleId, setStyleId] = useState<number | null>(null);
  const chosenStyle = styleId ?? styleList.data?.items[0]?.id;
  const nav = useNavigate();
  const [ideaId, setIdeaId] = useState("");
  const [instruction, setInstruction] = useState("");
  const [upload, setUpload] = useState<{ name: string; frac: number } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const [path, setPath] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const send = async (file: File) => {
    setMsg(null);
    setUpload({ name: file.name, frac: 0 });
    try {
      const p = await api.uploadEdit(file, { idea_id: ideaId ? Number(ideaId) : undefined, instruction: instruction || undefined, style_id: chosenStyle }, (frac) =>
        setUpload({ name: file.name, frac }),
      );
      nav(`/editor/${p.id}`);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setUpload(null);
    }
  };

  const importPath = async (e: React.FormEvent) => {
    e.preventDefault();
    setMsg(null);
    try {
      const p = await api.importEdit({ path: path.trim(), idea_id: ideaId ? Number(ideaId) : undefined, instruction: instruction || undefined, style_id: chosenStyle });
      nav(`/editor/${p.id}`);
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : String(err));
    }
  };

  const scripted = ideas.data?.items.filter((i) => i.has_script) || [];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Editor</h1>
          <p className="sub">Drop raw footage. Studio removes bad takes and dead air, reframes, captions, adds graphics and masters the audio.</p>
        </div>
      </div>

      <div
        className={`drop ${drag ? "drag" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          const f = e.dataTransfer.files?.[0];
          if (f) send(f);
        }}
        onClick={() => !upload && input.current?.click()}
      >
        <input ref={input} type="file" accept="video/*,.mov,.mp4,.m4v" hidden onChange={(e) => e.target.files?.[0] && send(e.target.files[0])} />
        {upload ? (
          <>
            <div className="drop-title">Uploading {upload.name}</div>
            <div className="upbar">
              <span style={{ width: `${Math.round(upload.frac * 100)}%` }} />
            </div>
            <div className="muted small">{Math.round(upload.frac * 100)}% — keep this tab open until it finishes</div>
          </>
        ) : (
          <>
            <div className="drop-title">Drop raw footage here</div>
            <div className="muted">or click to choose a file · iPhone HDR, 4K and long takes are fine</div>
          </>
        )}
      </div>

      {styleList.data && styleList.data.items.length > 0 && (
        <section className="card mt">
          <div className="card-head">
            <h2>Style</h2>
            <span className="muted small">{styleList.data.items.find((s) => s.id === chosenStyle)?.description}</span>
            <span className="spacer" />
            <Link to="/styles" className="link-more">
              All styles →
            </Link>
          </div>
          <StylePicker styles={styleList.data.items} value={chosenStyle} onPick={setStyleId} />
        </section>
      )}

      <div className="drop-opts" onClick={(e) => e.stopPropagation()}>
        <label className="field">
          <span>Script it was shot from (optional — makes take selection exact)</span>
          <select value={ideaId} onChange={(e) => setIdeaId(e.target.value)}>
            <option value="">No script — work it out from the speech</option>
            {scripted.map((i) => (
              <option key={i.id} value={i.id}>
                {i.title}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Direction for the editor (optional)</span>
          <input value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="e.g. “under 60 seconds”, “keep the recap at the end”" />
        </label>
        <form className="field" onSubmit={importPath}>
          <span>…or a file already on this computer</span>
          <div className="row gap">
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="~/Downloads/IMG_1234.MOV" style={{ flex: 1 }} />
            <button className="btn" disabled={!path.trim()}>
              Import
            </button>
          </div>
        </form>
      </div>
      {msg && <div className="alert alert-error">{msg}</div>}
      <ErrorBox error={error} />

      <h2 className="side-h" style={{ marginTop: 26 }}>
        Edits
      </h2>
      {data && data.length === 0 && <Empty title="No edits yet">Your first edit takes a few minutes: the footage is converted, transcribed, cut by Claude and rendered.</Empty>}
      <div className="edit-grid">
        {data?.map((p) => (
          <Link key={p.id} to={`/editor/${p.id}`} className="edit-card">
            <div className="edit-thumb">{p.thumb ? <img src={`${p.thumb}?v=${p.render_count}`} alt="" /> : <div className="vtile-noimg" />}</div>
            <div className="edit-body">
              <div className="strong">{p.title}</div>
              <EditStatus p={p} />
              <div className="muted small">
                {p.raw_duration ? `${mmss(p.raw_duration)} raw` : p.source_name}
                {p.duration ? ` → ${mmss(p.duration)} final` : ""} · {ago(p.created_at)}
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
