import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, type Integration, type RuleFlag } from "../api";
import { ErrorBox } from "../components/ui";
import { mmss, useLoad } from "../util";

type Draft = {
  integration: Integration;
  text: string;
  title: string;
  flags: RuleFlag[];
  settings: Record<string, string>;
};

const TITLE_PROVIDERS = new Set(["youtube", "tiktok"]);

function localInputValue(d: Date) {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function ProviderDot({ provider }: { provider: string }) {
  return <span className={`pdot pdot-${provider.split("-")[0]}`} />;
}

export default function PublishNew() {
  const editId = Number(useParams().editId);
  const nav = useNavigate();
  const edit = useLoad(() => api.edit(editId), [editId]);
  const status = useLoad(() => api.publishStatus(), []);
  const [picked, setPicked] = useState<Record<string, boolean>>({});
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [writing, setWriting] = useState(false);
  const [instr, setInstr] = useState("");
  const [when, setWhen] = useState<"slot" | "schedule" | "draft" | "now">("slot");
  const [date, setDate] = useState(() => localInputValue(new Date(Date.now() + 24 * 3600e3)));
  const [slot, setSlot] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const checkTimers = useRef<Record<string, number>>({});

  const channels = status.data?.integrations.filter((i) => !i.disabled) || [];
  const chosen = channels.filter((c) => picked[c.id]);

  useEffect(() => {
    const first = chosen[0];
    if (when !== "slot" || !first) return;
    api.publishSlot(first.id).then((r) => setSlot(r.date)).catch(() => setSlot(null));
  }, [when, chosen.map((c) => c.id).join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const allFlags = useMemo(() => chosen.flatMap((c) => (drafts[c.id]?.flags || []).map((f) => ({ ...f, where: c.name }))), [chosen, drafts]);

  if (!edit.data || !status.data) return <div className="page">{edit.error || status.error ? <ErrorBox error={edit.error || status.error} /> : <div className="loading">Loading…</div>}</div>;
  const e = edit.data;
  const st = status.data;

  const write = async () => {
    setWriting(true);
    setMsg(null);
    try {
      const providers = [...new Set(chosen.map((c) => c.provider))];
      const res = await api.publishCaptions(editId, providers, instr || undefined);
      const by = Object.fromEntries(res.captions.map((c) => [c.provider, c]));
      const next = { ...drafts };
      for (const c of chosen) {
        const cap = by[c.provider];
        next[c.id] = { integration: c, text: cap?.text || "", title: cap?.title || e.title, flags: cap?.flags || [], settings: drafts[c.id]?.settings || {} };
      }
      setDrafts(next);
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : String(err));
    } finally {
      setWriting(false);
    }
  };

  const update = (id: string, patch: Partial<Draft>) => {
    setDrafts((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
    if (patch.text !== undefined || patch.title !== undefined) {
      window.clearTimeout(checkTimers.current[id]);
      checkTimers.current[id] = window.setTimeout(async () => {
        const cur = { ...drafts[id], ...patch };
        const r = await api.publishCheck(cur.text, cur.title);
        setDrafts((d) => ({ ...d, [id]: { ...d[id], flags: r.flags } }));
      }, 500);
    }
  };

  const whenLabel = () => {
    if (when === "now") return "right now";
    if (when === "draft") return "as a draft in Postiz (nothing goes live)";
    const iso = when === "slot" ? slot : new Date(date).toISOString();
    return iso ? new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
  };

  const ready = chosen.length > 0 && chosen.every((c) => drafts[c.id]?.text?.trim()) && (when !== "slot" || slot);

  const submit = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const body = {
        edit_id: editId,
        when: when === "slot" ? "schedule" : when,
        date: when === "slot" ? slot : when === "schedule" ? new Date(date).toISOString() : null,
        channels: chosen.map((c) => {
          const d = drafts[c.id];
          const settings: Record<string, unknown> = { ...d.settings };
          return { integration_id: c.id, provider: c.provider, name: c.name, picture: c.picture, text: d.text, title: d.title, settings };
        }),
      };
      await api.createPublication(body);
      nav("/publish");
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : String(err));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <Link to={`/editor/${editId}`} className="back">
        ← {e.title}
      </Link>
      <div className="page-head">
        <div>
          <h1>Publish</h1>
          <p className="sub">
            {e.title} · {mmss(e.duration)} · goes out through Postiz
          </p>
        </div>
      </div>

      {!st.configured && <div className="alert alert-error">Postiz isn't configured. Add POSTIZ_URL and POSTIZ_API_KEY to .env.</div>}
      {st.error && <div className="alert alert-error">{st.error}</div>}
      {e.status !== "ready" && <div className="alert alert-warn">This edit is still rendering. Publish once it's ready.</div>}

      <div className="detail">
        <div className="detail-media">{e.has_output && <video src={`/api/edits/${e.id}/video?v=${e.render_count}`} controls playsInline />}</div>
        <div className="detail-main">
          <section className="card">
            <div className="card-head">
              <h2>Channels</h2>
              <span className="spacer" />
              <Link to="/publish/setup" className="link-more">
                Connect channels →
              </Link>
            </div>
            {channels.length === 0 ? (
              <div className="empty slim">
                <div className="empty-title">No channels connected yet</div>
                <div className="empty-body">
                  Connect TikTok, Instagram, YouTube and the rest in Postiz first. <Link to="/publish/setup">See the setup checklist</Link>.
                </div>
              </div>
            ) : (
              <div className="chan-grid">
                {channels.map((c) => (
                  <label key={c.id} className={`chan ${picked[c.id] ? "on" : ""}`}>
                    <input type="checkbox" checked={!!picked[c.id]} onChange={(ev) => setPicked((p) => ({ ...p, [c.id]: ev.target.checked }))} />
                    {c.picture ? <img src={c.picture} alt="" referrerPolicy="no-referrer" /> : <ProviderDot provider={c.provider} />}
                    <div>
                      <div className="strong">{c.name}</div>
                      <div className="muted small">{c.label}</div>
                    </div>
                  </label>
                ))}
              </div>
            )}
            {chosen.length > 0 && (
              <form
                className="instr-row"
                onSubmit={(ev) => {
                  ev.preventDefault();
                  write();
                }}
              >
                <input value={instr} onChange={(ev) => setInstr(ev.target.value)} placeholder="Optional direction for the captions" disabled={writing} />
                <button className="btn btn-primary" disabled={writing}>
                  {writing ? "Writing…" : Object.keys(drafts).length ? "Rewrite captions" : "Write captions"}
                </button>
              </form>
            )}
          </section>

          {chosen.map((c) => {
            const d = drafts[c.id];
            if (!d) return null;
            return (
              <section key={c.id} className="card">
                <div className="card-head">
                  <ProviderDot provider={c.provider} />
                  <h2>{c.name}</h2>
                  <span className="muted small">{c.label}</span>
                  <span className="spacer" />
                  <span className="muted small">{d.text.length} chars</span>
                </div>
                {TITLE_PROVIDERS.has(c.provider) && (
                  <label className="field">
                    <span>Title</span>
                    <input value={d.title} maxLength={c.provider === "youtube" ? 100 : 90} onChange={(ev) => update(c.id, { title: ev.target.value })} />
                  </label>
                )}
                <label className="field">
                  <span>Caption</span>
                  <textarea rows={c.provider.startsWith("linkedin") ? 9 : 5} value={d.text} onChange={(ev) => update(c.id, { text: ev.target.value })} />
                </label>
                {c.provider === "tiktok" && (
                  <div className="opts">
                    <label className="check">
                      Who can watch
                      <select value={d.settings.privacy_level || "PUBLIC_TO_EVERYONE"} onChange={(ev) => update(c.id, { settings: { ...d.settings, privacy_level: ev.target.value } })}>
                        <option value="PUBLIC_TO_EVERYONE">Everyone</option>
                        <option value="FOLLOWER_OF_CREATOR">Followers</option>
                        <option value="MUTUAL_FOLLOW_FRIENDS">Friends</option>
                        <option value="SELF_ONLY">Only me (test)</option>
                      </select>
                    </label>
                  </div>
                )}
                {c.provider === "youtube" && (
                  <div className="opts">
                    <label className="check">
                      Visibility
                      <select value={d.settings.type || "public"} onChange={(ev) => update(c.id, { settings: { ...d.settings, type: ev.target.value } })}>
                        <option value="public">Public</option>
                        <option value="unlisted">Unlisted</option>
                        <option value="private">Private (test)</option>
                      </select>
                    </label>
                  </div>
                )}
                {d.flags.length > 0 && (
                  <div className="alert alert-error mt">
                    {d.flags.map((f, k) => (
                      <div key={k}>
                        “{f.term}” — {f.why}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            );
          })}

          {chosen.length > 0 && Object.keys(drafts).length > 0 && (
            <section className="card">
              <div className="card-head">
                <h2>When</h2>
              </div>
              <div className="when">
                <label className="check">
                  <input type="radio" checked={when === "slot"} onChange={() => setWhen("slot")} /> Next free slot{" "}
                  {when === "slot" && <span className="muted">({slot ? new Date(slot).toLocaleString() : "finding…"})</span>}
                </label>
                <label className="check">
                  <input type="radio" checked={when === "schedule"} onChange={() => setWhen("schedule")} /> Pick a time
                  {when === "schedule" && <input type="datetime-local" value={date} onChange={(ev) => setDate(ev.target.value)} />}
                </label>
                <label className="check">
                  <input type="radio" checked={when === "draft"} onChange={() => setWhen("draft")} /> Save as draft in Postiz
                </label>
                <label className="check">
                  <input type="radio" checked={when === "now"} onChange={() => setWhen("now")} /> Post now
                </label>
              </div>
              {msg && <div className="alert alert-error mt">{msg}</div>}
              <div className="row gap mt">
                <button className="btn btn-primary" disabled={!ready || allFlags.length > 0 || e.status !== "ready"} onClick={() => setConfirming(true)}>
                  {when === "draft" ? "Save draft…" : when === "now" ? "Post now…" : "Schedule…"}
                </button>
                {allFlags.length > 0 && <span className="warn-text small">Fix the brand-rule flags first</span>}
              </div>
            </section>
          )}
        </div>
      </div>

      {confirming && (
        <div className="modal-back" onClick={() => !busy && setConfirming(false)}>
          <div className="modal" onClick={(ev) => ev.stopPropagation()}>
            <h2>{when === "draft" ? "Save as draft?" : "Publish this video?"}</h2>
            <p className="muted">
              “{e.title}” goes to <strong>{chosen.map((c) => c.name).join(", ")}</strong> {whenLabel()}.
              {when !== "draft" && " Once it's live it's public on those accounts."}
            </p>
            <div className="row gap">
              <button className="btn btn-primary" disabled={busy} onClick={submit}>
                {busy ? "Sending…" : when === "draft" ? "Save draft" : when === "now" ? "Yes, post now" : "Yes, schedule it"}
              </button>
              <button className="btn btn-ghost" disabled={busy} onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
