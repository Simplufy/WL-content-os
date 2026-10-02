import { Link } from "react-router-dom";
import { api } from "../api";
import { ErrorBox } from "../components/ui";
import { useLoad } from "../util";
import { ProviderDot } from "./PublishNew";

const PLATFORMS: { key: string; name: string; console: string; docs: string; note: string }[] = [
  { key: "tiktok", name: "TikTok", console: "https://developers.tiktok.com/apps", docs: "https://docs.postiz.com/self-host/providers/tiktok",
    note: "Needs the Content Posting API. Until TikTok audits the app, posts can only be private (“Only me”)." },
  { key: "instagram", name: "Instagram + Facebook", console: "https://developers.facebook.com/apps", docs: "https://docs.postiz.com/self-host/providers/instagram",
    note: "One Meta app covers both. The Instagram account must be Business/Creator and linked to a Facebook Page." },
  { key: "youtube", name: "YouTube", console: "https://console.cloud.google.com/apis/credentials", docs: "https://docs.postiz.com/self-host/providers/youtube",
    note: "Google Cloud project with YouTube Data API v3 enabled and an OAuth client." },
  { key: "linkedin", name: "LinkedIn", console: "https://www.linkedin.com/developers/apps", docs: "https://docs.postiz.com/self-host/providers/linkedin",
    note: "Add the “Share on LinkedIn” and “Sign In with LinkedIn” products." },
  { key: "x", name: "X", console: "https://developer.x.com/en/portal/dashboard", docs: "https://docs.postiz.com/self-host/providers/x-twitter",
    note: "Video posting needs a paid API tier." },
];

export default function PublishSetup() {
  const { data: st, error } = useLoad(() => api.publishStatus(), []);
  if (!st) return <div className="page">{error ? <ErrorBox error={error} /> : <div className="loading">Loading…</div>}</div>;
  const ui = st.ui_url;
  const connectedProviders = new Set(st.integrations.map((i) => i.provider.split("-")[0]));

  return (
    <div className="page narrow">
      <Link to="/publish" className="back">
        ← Publish
      </Link>
      <div className="page-head">
        <h1>Channels &amp; setup</h1>
      </div>

      <section className="card">
        <div className="card-head">
          <h2>Postiz</h2>
          <span className={`status ${st.connected ? "status-done" : "status-failed"}`}>{st.connected ? "API connected" : "Not reachable"}</span>
        </div>
        <dl className="hook-dl">
          <dt>Address</dt>
          <dd>
            <a href={ui} target="_blank" rel="noreferrer">
              {ui}
            </a>{" "}
            {!st.public && <span className="tag tag-warn">local only</span>}
          </dd>
          <dt>Channels</dt>
          <dd>
            {st.integrations.length === 0
              ? "None connected yet"
              : st.integrations.map((i) => (
                  <span key={i.id} className="chan-chip">
                    <ProviderDot provider={i.provider} /> {i.name} <span className="muted small">({i.label})</span>
                    {i.disabled && <span className="tag tag-warn">disabled</span>}
                  </span>
                ))}
          </dd>
        </dl>
        {st.error && <div className="alert alert-error">{st.error}</div>}
      </section>

      <section className="card">
        <div className="card-head">
          <h2>1 · Give Postiz a public HTTPS address</h2>
          <span className={`status ${st.public ? "status-done" : ""}`}>{st.public ? "Done" : "To do"}</span>
        </div>
        <p className="muted">
          TikTok, Meta, Google and LinkedIn only send logins back to a public HTTPS address, and Instagram downloads the video from Postiz's
          public URL. A Cloudflare Tunnel does this without opening ports. Public sign-up on Postiz gets switched off at the same time.
        </p>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>2 · Create a developer app per platform</h2>
        </div>
        <p className="muted">
          Self-hosted Postiz uses your own app credentials. Each app needs the redirect URL{" "}
          <code>{st.public ? ui : "https://<your-postiz-domain>"}/integrations/social/&lt;platform&gt;</code>. Paste the client ID/secret into
          Postiz's settings, then connect the account from Postiz.
        </p>
        <div className="plat-list">
          {PLATFORMS.map((p) => (
            <div key={p.key} className="plat-row">
              <ProviderDot provider={p.key} />
              <div className="grow">
                <div className="strong">
                  {p.name} {connectedProviders.has(p.key) && <span className="status status-done">connected</span>}
                </div>
                <div className="muted small">{p.note}</div>
              </div>
              <a className="btn btn-sm" href={p.console} target="_blank" rel="noreferrer">
                Developer console ↗
              </a>
              <a className="btn btn-sm btn-ghost" href={p.docs} target="_blank" rel="noreferrer">
                Guide ↗
              </a>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>3 · Connect the accounts in Postiz</h2>
          <span className={`status ${st.integrations.length ? "status-done" : ""}`}>{st.integrations.length ? `${st.integrations.length} connected` : "To do"}</span>
        </div>
        <p className="muted">
          In Postiz, “Add channel” → pick the platform → log in. They appear here automatically and in the Publish screen.
        </p>
        <a className="btn btn-primary" href={ui} target="_blank" rel="noreferrer">
          Open Postiz ↗
        </a>
      </section>
    </div>
  );
}
