import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ErrorBox } from "../components/ui";
import { ago, useLoad } from "../util";

const KIND_LABEL: Record<string, string> = {
  check_creator: "Check creator",
  process_video: "Download + transcribe",
  analyze_video: "Claude teardown",
  refresh_metrics: "Refresh metrics",
};

export default function SystemPage() {
  const [status, setStatus] = useState("");
  const { data, error, reload } = useLoad(() => api.jobs(status || undefined), [status], 4000);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>System</h1>
          <p className="sub">Background jobs. Failed jobs retry automatically up to 3 times.</p>
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="tabs">
        {["", "running", "pending", "failed", "done"].map((s) => (
          <button key={s} className={`tab ${status === s ? "active" : ""}`} onClick={() => setStatus(s)}>
            {s || "All"} {s && data?.counts[s] ? <span className="count">{data.counts[s]}</span> : null}
          </button>
        ))}
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Job</th>
              <th>Target</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>When</th>
              <th>Error</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data?.items.map((j) => (
              <tr key={j.id}>
                <td>{KIND_LABEL[j.kind] || j.kind}</td>
                <td>
                  {j.ref_id &&
                    (j.kind === "check_creator" ? <Link to={`/creators/${j.ref_id}`}>creator #{j.ref_id}</Link> : <Link to={`/videos/${j.ref_id}`}>video #{j.ref_id}</Link>)}
                </td>
                <td>
                  <span className={`status status-${j.status === "running" ? "downloading" : j.status}`}>
                    {j.status === "running" && <span className="spinner" />}
                    {j.status}
                  </span>
                </td>
                <td className="num">
                  {j.attempts}/{j.max_attempts}
                </td>
                <td className="muted small">
                  {j.status === "pending" ? `runs ${ago(j.run_after)}` : ago(j.finished_at || j.started_at || j.created_at)}
                </td>
                <td className="err-cell" title={j.error || ""}>
                  {j.error}
                </td>
                <td>
                  {j.status === "failed" && (
                    <button className="btn btn-sm" onClick={() => api.retryJob(j.id).then(reload)}>
                      Retry
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
