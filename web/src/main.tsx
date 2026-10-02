import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, type LlmStatus } from "./api";
import CreatorDetail from "./pages/CreatorDetail";
import Creators from "./pages/Creators";
import DashboardPage from "./pages/Dashboard";
import Hooks from "./pages/Hooks";
import EditDetail from "./pages/EditDetail";
import Editor from "./pages/Editor";
import IdeaDetail from "./pages/IdeaDetail";
import Login from "./pages/Login";
import Ideas from "./pages/Ideas";
import Prompter from "./pages/Prompter";
import Publish from "./pages/Publish";
import PublishNew from "./pages/PublishNew";
import PublishSetup from "./pages/PublishSetup";
import SettingsPage from "./pages/Settings";
import Styles from "./pages/Styles";
import SystemPage from "./pages/System";
import VideoDetail from "./pages/VideoDetail";
import Videos from "./pages/Videos";
import { BrandProvider, useBrand } from "./brand";
import "./styles.css";

const NAV: { to: string; label: string; icon: string; soon?: string }[] = [
  { to: "/", label: "Dashboard", icon: "◧" },
  { to: "/creators", label: "Creators", icon: "◎" },
  { to: "/videos", label: "Videos", icon: "▶" },
  { to: "/hooks", label: "Hooks", icon: "❝" },
  { to: "/ideas", label: "Ideas & Scripts", icon: "✎" },
  { to: "/editor", label: "Editor", icon: "✂" },
  { to: "/styles", label: "Styles", icon: "◐" },
  { to: "/publish", label: "Publish", icon: "↗" },
];

function StatusDock() {
  const [llm, setLlm] = useState<LlmStatus | null>(null);
  const [queue, setQueue] = useState<number>(0);
  const loc = useLocation();
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .dashboard()
        .then((d) => {
          if (!alive) return;
          setLlm(d.llm);
          setQueue(d.kpis.in_queue);
        })
        .catch(() => {});
    load();
    const t = setInterval(load, 10000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [loc.pathname]);
  const llmTone = llm?.ok === true ? "ok" : llm?.ok === false ? "bad" : "unknown";
  return (
    <div className="dock">
      <NavLink to="/settings" className={`dock-row dock-${llmTone}`} title={llm?.detail}>
        <span className="dot" /> Claude {llm?.ok === true ? "connected" : llm?.ok === false ? "needs login" : "not checked"}
      </NavLink>
      <NavLink to="/system" className="dock-row">
        <span className={`dot ${queue ? "dot-busy" : ""}`} /> {queue ? `${queue} job${queue === 1 ? "" : "s"} running` : "Queue idle"}
      </NavLink>
    </div>
  );
}

function Soon({ phase, title }: { phase: string; title: string }) {
  return (
    <div className="page">
      <div className="page-head">
        <h1>{title}</h1>
      </div>
      <div className="empty">
        <div className="empty-title">Coming in {phase}</div>
        <div className="empty-body">This section is part of the build plan and is not wired up yet.</div>
      </div>
    </div>
  );
}

function Account() {
  const [me, setMe] = useState<Awaited<ReturnType<typeof api.me>> | null>(null);
  useEffect(() => {
    api.me().then((m) => {
      setMe(m);
      if (!m.local && !m.user) location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    }).catch(() => {});
  }, []);
  if (!me?.user) return null;
  return (
    <div className="dock-row account">
      <span className="ellipsis">{me.user.email}</span>
      <button className="btn btn-sm btn-ghost" onClick={() => api.logout().then(() => (location.href = "/login"))}>
        Sign out
      </button>
    </div>
  );
}

function App() {
  const brand = useBrand();
  const [navOpen, setNavOpen] = useState(false);
  const loc = useLocation();
  useEffect(() => setNavOpen(false), [loc.pathname]);
  return (
    <div className="shell">
      <aside className={`side ${navOpen ? "open" : ""}`}>
        <div className="brand">
          <span className="brand-mark">{brand.logo_initials}</span>
          <div>
            <div className="brand-name">{brand.product_name}</div>
            <div className="brand-sub">{brand.org_name}</div>
          </div>
        </div>
        <nav>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => `nav${isActive ? " active" : ""}${n.soon ? " nav-soon" : ""}`}>
              <span className="nav-icon">{n.icon}</span>
              <span>{n.label}</span>
              {n.soon && <span className="nav-badge">{n.soon}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <NavLink to="/settings" className={({ isActive }) => `nav${isActive ? " active" : ""}`}>
            <span className="nav-icon">⚙</span>
            <span>Settings</span>
          </NavLink>
          <NavLink to="/system" className={({ isActive }) => `nav${isActive ? " active" : ""}`}>
            <span className="nav-icon">≡</span>
            <span>System</span>
          </NavLink>
          <StatusDock />
          <Account />
        </div>
      </aside>
      <button className="nav-toggle" onClick={() => setNavOpen((o) => !o)} aria-label="Menu">
        ☰
      </button>
      <main className="main">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/creators" element={<Creators />} />
          <Route path="/creators/:id" element={<CreatorDetail />} />
          <Route path="/videos" element={<Videos />} />
          <Route path="/videos/:id" element={<VideoDetail />} />
          <Route path="/hooks" element={<Hooks />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/ideas" element={<Ideas />} />
          <Route path="/ideas/:id" element={<IdeaDetail />} />
          <Route path="/editor" element={<Editor />} />
          <Route path="/editor/:id" element={<EditDetail />} />
          <Route path="/styles" element={<Styles />} />
          <Route path="/publish" element={<Publish />} />
          <Route path="/publish/setup" element={<PublishSetup />} />
          <Route path="/publish/new/:editId" element={<PublishNew />} />
          <Route path="*" element={<Soon phase="—" title="Not found" />} />
        </Routes>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <BrandProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/ideas/:id/prompter" element={<Prompter />} />
        <Route path="*" element={<App />} />
      </Routes>
      </BrandProvider>
    </BrowserRouter>
  </StrictMode>,
);
