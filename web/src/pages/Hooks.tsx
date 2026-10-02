import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorBox, OutlierPill, ScorePill } from "../components/ui";
import { compact, HOOK_TYPE_LABEL, useLoad } from "../util";

export default function Hooks() {
  const [sort, setSort] = useState("score");
  const [type, setType] = useState("");
  const { data, error } = useLoad(() => api.hooks(sort, type || undefined), [sort, type]);
  const [copied, setCopied] = useState<number | null>(null);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Hook library</h1>
          <p className="sub">Every analyzed opener, with the reusable pattern behind it. Templates feed the script generator in Phase 2.</p>
        </div>
      </div>
      <div className="filters">
        <select value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="score">Sort: Overall score</option>
          <option value="hook">Sort: Hook score</option>
          <option value="outlier">Sort: Outlier</option>
        </select>
        <div className="chips">
          <button className={`chip chip-btn ${type === "" ? "active" : ""}`} onClick={() => setType("")}>
            All
          </button>
          {Object.entries(HOOK_TYPE_LABEL).map(([k, v]) => (
            <button key={k} className={`chip chip-btn ${type === k ? "active" : ""}`} onClick={() => setType(k)}>
              {v}
            </button>
          ))}
        </div>
      </div>
      <ErrorBox error={error} />
      {data && data.length === 0 && <Empty title="No hooks yet">Hooks appear here once posts finish their Claude teardown.</Empty>}
      <div className="hook-cards">
        {data?.map((h) => (
          <div key={h.id} className="hook-card">
            <div className="hook-card-top">
              <ScorePill score={h.score} label="Overall" />
              <span className="tag">{HOOK_TYPE_LABEL[h.hook.type || ""] || h.hook.type}</span>
              <span className="spacer" />
              <OutlierPill x={h.outlier} />
            </div>
            <Link to={`/videos/${h.id}`} className="hook-quote big">
              “{h.hook.spoken}”
            </Link>
            {h.hook.onscreen_text && <div className="onscreen">On screen: {h.hook.onscreen_text}</div>}
            <div className="template-row">
              <span className="template">{h.hook.template}</span>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => {
                  navigator.clipboard.writeText(h.hook.template || "");
                  setCopied(h.id);
                  setTimeout(() => setCopied(null), 1500);
                }}
              >
                {copied === h.id ? "Copied" : "Copy"}
              </button>
            </div>
            <div className="muted small">
              @{h.handle} · {compact(h.views)} views · hook {h.hook.score}/100 · {h.topic}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
