import { useEffect, useState } from "react";
import { api, ApiError, type Platform } from "../api";
import Team from "../components/Team";
import { ErrorBox } from "../components/ui";
import { ago, useLoad } from "../util";

const COOKIE_HELP: Record<Platform, string> = {
  instagram: "Required. Instagram blocks profile listing without a logged-in session. Use a secondary account — scraping can get an account flagged.",
  tiktok: "Optional. Only needed if TikTok starts blocking anonymous requests.",
  youtube: "Optional. Only needed if YouTube asks to confirm you're not a bot.",
};

export default function SettingsPage() {
  const { data, error, setData } = useLoad(() => api.settings(), []);
  const me = useLoad(() => api.me(), []);
  const [brief, setBrief] = useState("");
  const [saved, setSaved] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [pinging, setPinging] = useState(false);
  const [pillars, setPillars] = useState("");
  const [testing, setTesting] = useState<Platform | null>(null);

  useEffect(() => {
    if (data) setBrief(data.brand_brief);
  }, [data?.brand_brief]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (data) setPillars(data.content_pillars.join("\n"));
  }, [data?.content_pillars.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!data) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="loading">Loading…</div>}</div>;

  const upload = async (p: Platform, f: File | undefined) => {
    if (!f) return;
    setMsg(null);
    try {
      setData(await api.uploadCookies(p, f));
      setMsg(`${p} cookies saved. Re-checking ${p} creators now.`);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    }
  };

  const setBrowser = async (p: Platform, b: string) => {
    setMsg(null);
    try {
      setData(await api.setBrowserCookies(p, b || null));
      if (b !== "off") setMsg(b === "auto" ? `${p}: will use whichever browser on this machine is signed in.` : `${p} will use the ${b} login on this machine.`);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    }
  };
  const test = async (p: Platform) => {
    setTesting(p);
    setMsg(null);
    try {
      const r = await api.testCookies(p);
      setMsg(`${p}: ${r.ok ? "✓" : "✗"} ${r.detail}`);
      setData(await api.settings());
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="page narrow">
      <div className="page-head">
        <h1>Settings</h1>
      </div>

      {me.data && <Team me={me.data} />}

      <section className="card">
        <div className="card-head">
          <h2>Claude</h2>
          <span className={`status ${data.llm.ok ? "status-done" : data.llm.ok === false ? "status-failed" : ""}`}>
            {data.llm.ok ? "Connected" : data.llm.ok === false ? "Not connected" : "Not checked"}
          </span>
        </div>
        <p className="muted">
          Teardowns, hook scores and (next phase) scripts run on your Claude subscription through the Claude Code CLI on this
          machine. Model: <code>{data.llm_model}</code>. Last check {ago(data.llm.at)}.
        </p>
        {data.llm.ok === false && (
          <div className="alert alert-warn">
            <div>{data.llm.detail}</div>
            <div className="howto">
              To connect, run this once in a terminal on this machine and follow the browser login:
              <pre>claude setup-token</pre>
              Then save the token it prints into <code>.env</code> in the install folder as{" "}
              <code>CLAUDE_CODE_OAUTH_TOKEN=…</code> and restart Studio (<code>systemctl --user restart content-studio-worker</code>).
            </div>
          </div>
        )}
        <button
          className="btn"
          disabled={pinging}
          onClick={async () => {
            setPinging(true);
            try {
              const llm = await api.pingLlm();
              setData({ ...data, llm });
            } finally {
              setPinging(false);
            }
          }}
        >
          {pinging ? "Testing…" : "Test connection"}
        </button>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Creator monitoring</h2>
        </div>
        <p className="muted">
          When a creator is added, Studio backfills by analyzing their best and newest past posts. After that it watches for new uploads.
        </p>
        <div className="opts">
          <label className="check">
            Backfill: top
            <input type="number" min={0} max={30} style={{ width: 70 }} defaultValue={data.backfill_top}
              onBlur={async (e) => setData(await api.saveSettings({ backfill_top: Number(e.target.value) }))} />
            by views
          </label>
          <label className="check">
            + newest
            <input type="number" min={0} max={30} style={{ width: 70 }} defaultValue={data.backfill_newest}
              onBlur={async (e) => setData(await api.saveSettings({ backfill_newest: Number(e.target.value) }))} />
          </label>
        </div>
        <label className="check mt">
          <input type="checkbox" checked={data.new_posts_only} onChange={async (e) => setData(await api.saveSettings({ new_posts_only: e.target.checked }))} />
          After the backfill, only pull in newly posted videos (ignore older posts that show up later)
        </label>
        <label className="check mt">
          <input type="checkbox" checked={data.auto_styles} onChange={async (e) => setData(await api.saveSettings({ auto_styles: e.target.checked }))} />
          Keep learning editing styles: fingerprint every new teardown and evolve the styles when 10+ new videos are in
        </label>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Brand brief</h2>
          {data.brand_brief_is_default && <span className="tag tag-warn">Draft — please edit</span>}
        </div>
        <p className="muted">Claude uses this to judge relevance and to suggest how your brand could use each video's mechanism.</p>
        <textarea rows={7} value={brief} onChange={(e) => setBrief(e.target.value)} />
        <div className="row gap">
          <button
            className="btn btn-primary"
            disabled={brief === data.brand_brief}
            onClick={async () => {
              setData(await api.saveSettings({ brand_brief: brief }));
              setSaved(true);
              setTimeout(() => setSaved(false), 2000);
            }}
          >
            Save brief
          </button>
          {saved && <span className="muted">Saved</span>}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Content pillars</h2>
        </div>
        <p className="muted">One per line. Generated ideas are spread across these.</p>
        <textarea rows={6} value={pillars} onChange={(e) => setPillars(e.target.value)} />
        <div className="row gap">
          <button
            className="btn btn-primary"
            disabled={pillars === data.content_pillars.join("\n")}
            onClick={async () => setData(await api.saveSettings({ content_pillars: pillars.split("\n") }))}
          >
            Save pillars
          </button>
          <span className="spacer" />
          <label className="check">
            <input type="checkbox" checked={data.auto_ideas} onChange={async (e) => setData(await api.saveSettings({ auto_ideas: e.target.checked }))} />{" "}
            Generate 5 ideas automatically each day when new teardowns land
          </label>
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Platform cookies</h2>
        </div>
        <p className="muted">
          Just sign in to the account in any browser (Chrome, Safari, Firefox…) <b>on the computer Studio runs on</b> — on Auto, Studio
          finds the signed-in browser by itself and uses its live login, so it never goes stale. Hit <b>Test</b> to check. Uploading a{" "}
          <code>cookies.txt</code> still works as a fallback. Nothing leaves this machine.
        </p>
        {msg && <div className="alert alert-ok">{msg}</div>}
        {(Object.keys(COOKIE_HELP) as Platform[]).map((p) => (
          <div key={p} className="cookie-row">
            <div>
              <div className="strong cap">{p}</div>
              <div className="muted small">{COOKIE_HELP[p]}</div>
            </div>
            <div className="row gap">
              <span className={`status ${data.cookies[p] || data.browser_cookies[p].found || !["auto", "off"].includes(data.browser_cookies[p].mode) ? "status-done" : ""}`}>
                {data.cookies[p]
                  ? "File saved"
                  : data.browser_cookies[p].mode === "off"
                    ? "Off"
                    : data.browser_cookies[p].mode !== "auto"
                      ? `From ${data.browser_cookies[p].mode}`
                      : data.browser_cookies[p].found
                        ? `Auto · ${data.browser_cookies[p].found}`
                        : "Auto · not found yet"}
              </span>
              <select value={data.browser_cookies[p].mode} onChange={(e) => setBrowser(p, e.target.value)}>
                <option value="auto">Auto-detect browser</option>
                {data.browsers.map((b) => (
                  <option key={b} value={b}>
                    Only {b}
                  </option>
                ))}
                <option value="off">Off</option>
              </select>
              <label className="btn btn-sm">
                Upload
                <input type="file" accept=".txt,text/plain" hidden onChange={(e) => upload(p, e.target.files?.[0])} />
              </label>
              {(data.browser_cookies[p].mode !== "off" || data.cookies[p]) && (
                <button className="btn btn-sm" disabled={testing === p} onClick={() => test(p)}>
                  {testing === p ? "Testing…" : "Test"}
                </button>
              )}
              {data.cookies[p] && (
                <button className="btn btn-sm btn-ghost btn-danger" onClick={async () => setData(await api.deleteCookies(p))}>
                  Remove
                </button>
              )}
            </div>
          </div>
        ))}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Engine</h2>
        </div>
        <dl className="hook-dl">
          <dt>Transcription</dt>
          <dd>whisper.cpp (Vulkan GPU) · model {data.whisper_model || "missing"}</dd>
          <dt>Monitor</dt>
          <dd>Every {Math.round(data.check_interval_min / 60)} hours per creator · metrics re-pulled at 24h, 3d and 7d</dd>
        </dl>
      </section>
    </div>
  );
}
