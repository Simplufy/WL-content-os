import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, type Idea } from "../api";
import { Empty, ErrorBox } from "../components/ui";
import { ago, HOOK_TYPE_LABEL, useLoad } from "../util";

export const STAGE_LABEL: Record<string, string> = {
  idea: "Ideas",
  scripted: "Scripted",
  filmed: "Filmed",
  edited: "Edited",
  scheduled: "Scheduled",
  posted: "Posted",
};

export const MECH_LABEL: Record<string, string> = {
  reframe: "Reframe",
  diagnostic_question: "Diagnostic question",
  pov: "POV",
  contrarian_claim: "Contrarian",
  before_after: "Before / after",
  list: "List",
  curiosity_gap: "Curiosity gap",
  story: "Story",
};

export function FunnelTag({ f }: { f: string | null }) {
  if (!f) return null;
  return <span className={`funnel funnel-${f}`}>{f}</span>;
}

export function GenBadge({ status, label }: { status: string; label: string }) {
  if (status === "pending")
    return (
      <span className="status status-analyzing">
        <span className="spinner" /> {label}
      </span>
    );
  if (status === "waiting_llm") return <span className="status status-waiting_llm">Waiting for Claude</span>;
  if (status === "failed") return <span className="status status-failed">Failed</span>;
  return null;
}

export default function Ideas() {
  const [showArchived, setShowArchived] = useState(false);
  const { data, error, reload } = useLoad(() => api.ideas(showArchived), [showArchived], 5000);
  const [focus, setFocus] = useState("");
  const [count, setCount] = useState(5);
  const [pillar, setPillar] = useState("");
  const [funnel, setFunnel] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const nav = useNavigate();

  const generate = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api.generateIdeas({ count, focus: focus.trim() || undefined });
      setFocus("");
      reload();
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const addOwn = async () => {
    const title = prompt("Idea title");
    if (!title?.trim()) return;
    const idea = await api.createIdea(title.trim());
    nav(`/ideas/${idea.id}`);
  };

  if (!data) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="loading">Loading…</div>}</div>;
  const pending = data.batches.filter((b) => b.status === "pending" || b.status === "waiting_llm");
  const failed = data.batches.filter((b) => b.status === "failed").slice(0, 1);
  const items = data.items.filter((i) => (!pillar || i.pillar === pillar) && (!funnel || i.funnel === funnel));

  return (
    <div className="page wide">
      <div className="page-head">
        <div>
          <h1>Ideas &amp; Scripts</h1>
          <p className="sub">
            Concepts built from {data.library_size} analyzed posts. Pick a hook, generate the script, record it.
          </p>
        </div>
        <div className="row gap">
          <button className="btn" onClick={addOwn}>
            + My own idea
          </button>
        </div>
      </div>

      <form className="gen-bar card" onSubmit={generate}>
        <div className="gen-title">Generate ideas</div>
        <input
          value={focus}
          onChange={(e) => setFocus(e.target.value)}
          placeholder="Optional focus — e.g. “hiring your first employee”, “raising prices”, “a story from the early days”"
        />
        <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
          {[3, 5, 8, 10].map((n) => (
            <option key={n} value={n}>
              {n} ideas
            </option>
          ))}
        </select>
        <button className="btn btn-primary" disabled={busy || data.library_size === 0}>
          {busy ? "Queuing…" : "Generate"}
        </button>
      </form>
      {data.library_size === 0 && (
        <div className="alert alert-warn">
          No analyzed videos yet. <Link to="/creators">Add creators</Link> first — ideas are built from their teardowns.
        </div>
      )}
      {msg && <div className="alert alert-error">{msg}</div>}
      {pending.map((b) => (
        <div key={b.id} className="alert alert-info">
          <GenBadge status={b.status} label="Generating" /> {b.count} ideas{b.focus ? ` about “${b.focus}”` : ""}
          {b.source_video_ids.length ? ` from ${b.source_video_ids.length} picked video(s)` : ""} — about a minute.
        </div>
      ))}
      {failed.map((b) => (
        <div key={b.id} className="alert alert-error">
          Last idea batch failed: {b.error}
        </div>
      ))}

      <div className="filters">
        <div className="chips">
          <button className={`chip chip-btn ${pillar === "" ? "active" : ""}`} onClick={() => setPillar("")}>
            All pillars
          </button>
          {data.pillars.map((p) => (
            <button key={p} className={`chip chip-btn ${pillar === p ? "active" : ""}`} onClick={() => setPillar(p)}>
              {p}
            </button>
          ))}
        </div>
        <span className="spacer" />
        <select value={funnel} onChange={(e) => setFunnel(e.target.value)}>
          <option value="">All funnel stages</option>
          <option value="TOFU">TOFU — relatable</option>
          <option value="MOFU">MOFU — teaches</option>
          <option value="BOFU">BOFU — proof / apply</option>
        </select>
        <label className="check">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> Archived
        </label>
      </div>

      {data.items.length === 0 && !pending.length ? (
        <Empty title={showArchived ? "Nothing archived" : "No ideas yet"}>
          {showArchived ? null : "Hit Generate. Studio also makes 5 fresh ideas automatically each day once new teardowns come in."}
        </Empty>
      ) : (
        <div className="board">
          {data.stages.map((stage) => {
            const col = items.filter((i) => i.stage === stage);
            return (
              <div key={stage} className="board-col">
                <div className="board-head">
                  {STAGE_LABEL[stage] || stage} <span className="count">{col.length}</span>
                  {["edited", "scheduled", "posted"].includes(stage) && <span className="muted small"> · later phases</span>}
                </div>
                {col.map((i) => (
                  <IdeaCard key={i.id} i={i} />
                ))}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function IdeaCard({ i }: { i: Idea }) {
  const hook = i.hooks[i.chosen_hook ?? 0];
  return (
    <Link to={`/ideas/${i.id}`} className="idea-card">
      <div className="idea-top">
        {i.starred ? <span className="star">★</span> : null}
        <FunnelTag f={i.funnel} />
        {i.pillar && <span className="tag">{i.pillar}</span>}
        <span className="spacer" />
        <GenBadge status={i.script_status} label="Writing" />
      </div>
      <div className="idea-title">{i.title}</div>
      {hook && (
        <div className="idea-hook">
          “{hook.text}” <span className="muted small">· {HOOK_TYPE_LABEL[hook.type] || hook.type}</span>
        </div>
      )}
      <div className="idea-foot muted small">
        {i.script_summary ? `Script · ~${i.script_summary.duration_s}s` : `${i.hooks.length} hooks`}
        {i.originality && !i.originality.ok ? <span className="warn-text"> · overlap flagged</span> : null}
        {i.originality && i.originality.rules_ok === false ? <span className="warn-text"> · brand rule</span> : null}
        <span className="spacer" />
        {ago(i.updated_at)}
      </div>
    </Link>
  );
}
