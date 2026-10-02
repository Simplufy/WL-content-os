import { useState } from "react";
import { api, ApiError } from "../api";
import { useBrand } from "../brand";

export default function Login() {
  const brand = useBrand();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const next = new URLSearchParams(location.search).get("next") || "/";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      await api.login(email, password);
      location.href = next.startsWith("/") && !next.startsWith("//") ? next : "/";
    } catch (e2) {
      setErr(e2 instanceof ApiError ? e2.message : String(e2));
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="brand">
          <span className="brand-mark">{brand.logo_initials}</span>
          <div>
            <div className="brand-name">{brand.product_name}</div>
            <div className="brand-sub">{brand.org_name}</div>
          </div>
        </div>
        <label className="field">
          <span>Email</span>
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
        </label>
        <label className="field">
          <span>Password</span>
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {err && <div className="alert alert-error">{err}</div>}
        <button className="btn btn-primary block" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="muted small center">Accounts are created by an admin in Settings → Team.</p>
      </form>
    </div>
  );
}
