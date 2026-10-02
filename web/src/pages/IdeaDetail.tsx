import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, type Idea, type Script } from "../api";
import { ErrorBox, VideoTile } from "../components/ui";
import { HOOK_TYPE_LABEL, useLoad } from "../util";
import { FunnelTag, GenBadge, MECH_LABEL, STAGE_LABEL } from "./Ideas";

const SECTION_LABEL: Record<string, string> = {
  setup: "Setup",
  value: "Value",
  proof: "Proof",
  payoff: "Payoff",
  cta: "CTA",
};

export function scriptToText(s: Script): string {
  return [s.hook.spoken, ...s.lines.map((l) => l.text)].join("\n\n");
}

export default function IdeaDetail() {
  const id = Number(useParams().id);
  const nav = useNavigate();
  const { data: idea, error, reload, setData } = useLoad(() => api.idea(id), [id], 4000);
  const [hookInstr, setHookInstr] = useState("");
  const [scriptInstr, setScriptInstr] = useState("");
  const [editing, setEditing] = useState<Script | null>(null);
  const [notes, setNotes] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    if (idea && notes === null) setNotes(idea.notes || "");
  }, [idea, notes]);

  if (!idea) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="loading">Loading…</div>}</div>;

  const patch = async (body: Parameters<typeof api.patchIdea>[1]) => setData(await api.patchIdea(idea.id, body));
  const copy = (key: string, text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(key);
    setTimeout(() => setCopied(null), 1500);
  };
  const s = idea.script;
  const scriptBusy = idea.script_status === "pending" || idea.script_status === "waiting_llm";

  return (
    <div className="page">
      <Link to="/ideas" className="back">
        ← Ideas &amp; Scripts
      </Link>
      <div className="page-head">
        <div>
          <h1
            className="editable"
            contentEditable
            suppressContentEditableWarning
            onBlur={(e) => {
              const t = e.currentTarget.textContent?.trim();
              if (t && t !== idea.title) patch({ title: t });
            }}
          >
            {idea.title}
          </h1>
          <div className="row gap wrap muted">
            <FunnelTag f={idea.funnel} />
            {idea.pillar && <span className="tag">{idea.pillar}</span>}
            {idea.mechanism && <span className="tag">{MECH_LABEL[idea.mechanism] || idea.mechanism}</span>}
          </div>
        </div>
        <div className="row gap wrap">
          <select value={idea.stage} onChange={(e) => patch({ stage: e.target.value })}>
            {Object.entries(STAGE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v === "Ideas" ? "Idea" : v}
              </option>
            ))}
          </select>
          <button className={`btn ${idea.starred ? "btn-star" : ""}`} onClick={() => patch({ starred: !idea.starred })}>
            {idea.starred ? "★ Starred" : "☆ Star"}
          </button>
          <button className="btn btn-ghost" onClick={() => patch({ archived: !idea.archived })}>
            {idea.archived ? "Unarchive" : "Archive"}
          </button>
          <button
            className="btn btn-ghost btn-danger"
            onClick={async () => {
              if (confirm("Delete this idea and its script?")) {
                await api.deleteIdea(idea.id);
                nav("/ideas");
              }
            }}
          >
            Delete
          </button>
        </div>
      </div>

      <div className="idea-layout">
        <div className="idea-main">
          {(idea.angle || idea.why) && (
            <section className="card">
              <dl className="hook-dl">
                {idea.angle && (
                  <>
                    <dt>Angle</dt>
                    <dd>{idea.angle}</dd>
                  </>
                )}
                {idea.format && (
                  <>
                    <dt>Format</dt>
                    <dd>{idea.format}</dd>
                  </>
                )}
                {idea.why && (
                  <>
                    <dt>Why it works</dt>
                    <dd>{idea.why}</dd>
                  </>
                )}
              </dl>
            </section>
          )}

          <section className="card">
            <div className="card-head">
              <h2>Script</h2>
              {idea.script_version > 0 && <span className="muted small">v{idea.script_version}</span>}
              <GenBadge status={idea.script_status} label="Writing script" />
              <span className="spacer" />
              {s && !editing && (
                <>
                  <button className="btn btn-sm" onClick={() => copy("script", scriptToText(s))}>
                    {copied === "script" ? "Copied" : "Copy"}
                  </button>
                  <button className="btn btn-sm" onClick={() => setEditing(structuredClone(s))}>
                    Edit
                  </button>
                  <Link to={`/ideas/${idea.id}/prompter`} className="btn btn-sm btn-primary">
                    Teleprompter
                  </Link>
                </>
              )}
            </div>

            {idea.script_status === "failed" && <div className="alert alert-error">{idea.script_error}</div>}
            {idea.script_status === "waiting_llm" && <div className="alert alert-warn">{idea.script_error}</div>}

            {!s && !scriptBusy && (
              <div className="empty slim">
                <div className="empty-title">No script yet</div>
                <div className="empty-body">Pick your favorite hook on the right, then write the script.</div>
              </div>
            )}
            {!s && scriptBusy && <div className="loading">Writing the script — usually under a minute…</div>}

            {s && !editing && <ScriptView s={s} />}
            {editing && (
              <ScriptEditor
                s={editing}
                onChange={setEditing}
                onCancel={() => setEditing(null)}
                onSave={async () => {
                  setData(await api.saveScript(idea.id, editing));
                  setEditing(null);
                }}
              />
            )}

            {idea.originality?.rule_flags && idea.originality.rule_flags.length > 0 && s && !editing && (
              <div className="alert alert-error mt">
                <strong>Breaks a brand rule:</strong>
                <ul className="tight">
                  {idea.originality.rule_flags.map((f, k) => (
                    <li key={k}>
                      {f.where}: “{f.term}” — {f.why}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {idea.originality && s && !editing && (
              <div className={`alert ${idea.originality.ok ? "alert-ok" : "alert-warn"} mt`}>
                {idea.originality.ok ? (
                  <>Original — no 6-word runs shared with any of {idea.originality.checked_against} competitor transcripts.</>
                ) : (
                  <>
                    <strong>Too close to a competitor:</strong>
                    <ul className="tight">
                      {idea.originality.matches.map((m, k) => (
                        <li key={k}>
                          {m.line}: “{m.phrase}…” — <Link to={`/videos/${m.video_id}`}>@{m.handle}</Link>
                        </li>
                      ))}
                    </ul>
                    Rewrite those lines (Edit) or ask for a rewrite below.
                  </>
                )}
              </div>
            )}

            {!editing && (
              <form
                className="instr-row"
                onSubmit={async (e) => {
                  e.preventDefault();
                  await api.writeScript(idea.id, scriptInstr || undefined);
                  setScriptInstr("");
                  reload();
                }}
              >
                <input
                  value={scriptInstr}
                  onChange={(e) => setScriptInstr(e.target.value)}
                  placeholder={s ? "Rewrite instruction — e.g. “shorter, more aggressive”, “add a story from the field”" : "Optional direction for the script"}
                  disabled={scriptBusy}
                />
                <button className={`btn ${s ? "" : "btn-primary"}`} disabled={scriptBusy}>
                  {s ? "Rewrite" : "Write script"}
                </button>
              </form>
            )}
          </section>

          {s && !editing && (
            <section className="card">
              <div className="card-head">
                <h2>Post copy</h2>
                <span className="spacer" />
                <button className="btn btn-sm" onClick={() => copy("caption", `${s.caption}\n\n${s.hashtags.map((h) => `#${h.replace(/^#/, "")}`).join(" ")}`)}>
                  {copied === "caption" ? "Copied" : "Copy"}
                </button>
              </div>
              <p className="pre">{s.caption}</p>
              <div className="chips">
                {s.hashtags.map((h) => (
                  <span key={h} className="chip">
                    #{h.replace(/^#/, "")}
                  </span>
                ))}
              </div>
              {s.filming_notes && (
                <>
                  <h3 className="h3">Filming notes</h3>
                  <p>{s.filming_notes}</p>
                </>
              )}
            </section>
          )}

          <section className="card">
            <div className="card-head">
              <h2>Notes</h2>
            </div>
            <textarea
              rows={3}
              value={notes ?? ""}
              onChange={(e) => setNotes(e.target.value)}
              onBlur={() => notes !== (idea.notes || "") && patch({ notes: notes || "" })}
              placeholder="Anything for the shoot — location, props, who's in it…"
            />
          </section>
        </div>

        <div className="idea-side">
          <section className="card">
            <div className="card-head">
              <h2>Hooks</h2>
              <GenBadge status={idea.hooks_status} label="Writing hooks" />
            </div>
            <HookPicker idea={idea} onPick={(k) => patch({ chosen_hook: k })} />
            <form
              className="instr-row"
              onSubmit={async (e) => {
                e.preventDefault();
                await api.moreHooks(idea.id, hookInstr || undefined);
                setHookInstr("");
                reload();
              }}
            >
              <input value={hookInstr} onChange={(e) => setHookInstr(e.target.value)} placeholder="Optional: “more questions”, “punchier”" />
              <button className="btn btn-sm" disabled={idea.hooks_status === "pending"}>
                +8 hooks
              </button>
            </form>
          </section>

          {idea.inspired_videos && idea.inspired_videos.length > 0 && (
            <section>
              <h2 className="side-h">Inspired by</h2>
              <div className="vgrid vgrid-side">
                {idea.inspired_videos.map((v) => (
                  <VideoTile key={v.id} v={v} />
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function HookPicker({ idea, onPick }: { idea: Idea; onPick: (k: number) => void }) {
  if (!idea.hooks.length) return <div className="muted small">No hooks yet — generate some below.</div>;
  return (
    <div className="hook-pick">
      {idea.hooks.map((h, k) => (
        <label key={k} className={`hook-opt ${idea.chosen_hook === k ? "chosen" : ""}`}>
          <input type="radio" name="hook" checked={idea.chosen_hook === k} onChange={() => onPick(k)} />
          <div>
            <div className="hook-opt-text">“{h.text}”</div>
            <div className="muted small">
              {HOOK_TYPE_LABEL[h.type] || h.type}
              {h.onscreen_text ? ` · on screen: ${h.onscreen_text}` : ""}
              {h.source_video_id ? (
                <>
                  {" · "}
                  <Link to={`/videos/${h.source_video_id}`} onClick={(e) => e.stopPropagation()}>
                    pattern source
                  </Link>
                </>
              ) : null}
            </div>
          </div>
        </label>
      ))}
    </div>
  );
}

function ScriptView({ s }: { s: Script }) {
  let lastSection = "";
  return (
    <div className="script">
      <div className="script-meta muted small">
        ~{s.duration_s}s · {s.word_count ?? "?"} words · {s.lines.length + 1} takes
      </div>
      <div className="script-block hook-block">
        <div className="script-sec">Hook</div>
        <div className="script-line">
          <span className="lid">H</span>
          <div>
            <div className="say">{s.hook.spoken}</div>
            {s.hook.onscreen_text && <div className="cue">On screen: {s.hook.onscreen_text}</div>}
            {s.hook.visual && <div className="cue">Visual: {s.hook.visual}</div>}
          </div>
        </div>
      </div>
      {s.lines.map((l) => {
        const head = l.section !== lastSection ? SECTION_LABEL[l.section] || l.section : null;
        lastSection = l.section;
        return (
          <div key={l.id}>
            {head && <div className="script-sec">{head}</div>}
            <div className="script-line">
              <span className="lid">{l.id}</span>
              <div>
                <div className="say">{l.text}</div>
                {l.onscreen_text && <div className="cue">On screen: {l.onscreen_text}</div>}
                {l.broll && <div className="cue">B-roll: {l.broll}</div>}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ScriptEditor({ s, onChange, onCancel, onSave }: { s: Script; onChange: (s: Script) => void; onCancel: () => void; onSave: () => void }) {
  const setLine = (k: number, text: string) => onChange({ ...s, lines: s.lines.map((l, i) => (i === k ? { ...l, text } : l)) });
  return (
    <div className="script-edit">
      <label className="field">
        <span>Hook</span>
        <textarea rows={2} value={s.hook.spoken} onChange={(e) => onChange({ ...s, hook: { ...s.hook, spoken: e.target.value } })} />
      </label>
      {s.lines.map((l, k) => (
        <div key={k} className="edit-line">
          <span className="lid">{l.id}</span>
          <textarea rows={2} value={l.text} onChange={(e) => setLine(k, e.target.value)} />
          <button className="btn btn-sm btn-ghost btn-danger" title="Remove line" onClick={() => onChange({ ...s, lines: s.lines.filter((_, i) => i !== k) })}>
            ✕
          </button>
        </div>
      ))}
      <button
        className="btn btn-sm btn-ghost"
        onClick={() => onChange({ ...s, lines: [...s.lines, { id: "", section: "value", text: "", onscreen_text: "", broll: "" }] })}
      >
        + Add line
      </button>
      <label className="field">
        <span>Caption</span>
        <textarea rows={3} value={s.caption} onChange={(e) => onChange({ ...s, caption: e.target.value })} />
      </label>
      <div className="row gap">
        <button className="btn btn-primary" onClick={onSave}>
          Save script
        </button>
        <button className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}
