import { useState } from "react";
import { api, ApiError, type Me } from "../api";
import { ago, useLoad } from "../util";

export default function Team({ me }: { me: Me }) {
  const canManage = me.local || !!me.user?.is_admin;
  const users = useLoad(() => (canManage ? api.teamUsers() : Promise.resolve([])), [canManage]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [admin, setAdmin] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [newPw, setNewPw] = useState("");

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await api.addTeamUser({ email, password: pw, name: name || undefined, is_admin: admin });
      setMsg({ ok: true, text: `Account created for ${email}. Share the password with them privately.` });
      setEmail("");
      setName("");
      setPw("");
      setAdmin(false);
      users.reload();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : String(err) });
    }
  };

  return (
    <section className="card">
      <div className="card-head">
        <h2>Team</h2>
        <span className="muted small">{me.user ? `Signed in as ${me.user.email}` : me.local ? "On this computer — no sign-in needed" : ""}</span>
      </div>
      <p className="muted">
        Anyone opening Studio from the internet (your public URL) needs an account. On this computer it opens without signing in.
      </p>
      {canManage && (
        <>
          <table className="table compact">
            <tbody>
              {users.data?.map((u) => (
                <tr key={u.id}>
                  <td>
                    <span className="strong">{u.email}</span> {u.name ? <span className="muted">· {u.name}</span> : null} {u.is_admin ? <span className="tag">admin</span> : null}
                  </td>
                  <td className="muted small">{u.last_login_at ? `last in ${ago(u.last_login_at)}` : "never signed in"}</td>
                  <td className="actions">
                    {me.user?.id !== u.id && (
                      <button className="btn btn-sm btn-ghost btn-danger" onClick={() => confirm(`Remove ${u.email}?`) && api.removeTeamUser(u.id).then(users.reload)}>
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <form className="team-add" onSubmit={add}>
            <input type="email" placeholder="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <input placeholder="name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
            <input type="password" placeholder="password (10+ chars)" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={10} autoComplete="new-password" />
            <label className="check">
              <input type="checkbox" checked={admin} onChange={(e) => setAdmin(e.target.checked)} /> admin
            </label>
            <button className="btn btn-primary">Add</button>
          </form>
        </>
      )}
      {msg && <div className={`alert ${msg.ok ? "alert-ok" : "alert-error"} mt`}>{msg.text}</div>}
      {me.user && (
        <form
          className="team-add mt"
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              await api.changePassword(newPw);
              setMsg({ ok: true, text: "Password changed. Other sessions were signed out." });
              setNewPw("");
            } catch (err) {
              setMsg({ ok: false, text: err instanceof ApiError ? err.message : String(err) });
            }
          }}
        >
          <input type="password" placeholder="new password for your account" value={newPw} onChange={(e) => setNewPw(e.target.value)} minLength={10} autoComplete="new-password" />
          <button className="btn">Change my password</button>
        </form>
      )}
    </section>
  );
}
