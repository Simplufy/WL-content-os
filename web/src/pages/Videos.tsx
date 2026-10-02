import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorBox, VideoTile } from "../components/ui";
import { HOOK_TYPE_LABEL, useLoad } from "../util";

export default function Videos() {
  const [params, setParams] = useSearchParams();
  const get = (k: string, d = "") => params.get(k) ?? d;
  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    setParams(p, { replace: true });
  };
  const query = {
    sort: get("sort", "score"),
    creator_id: get("creator_id") || undefined,
    min_outlier: get("min_outlier") || undefined,
    days: get("days") || undefined,
    hook_type: get("hook_type") || undefined,
    analyzed: get("analyzed") || undefined,
    q: get("q") || undefined,
    limit: 120,
  };
  const { data, error } = useLoad(() => api.videos(query), [params.toString()], 8000);
  const creators = useLoad(() => api.creators(), []);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Videos</h1>
          <p className="sub">{data ? `${data.total} posts` : " "}</p>
        </div>
      </div>
      <div className="filters">
        <input placeholder="Search captions & transcripts" defaultValue={get("q")} onKeyDown={(e) => e.key === "Enter" && set("q", e.currentTarget.value)} />
        <select value={query.sort} onChange={(e) => set("sort", e.target.value)}>
          <option value="score">Sort: Overall score</option>
          <option value="outlier">Sort: Outlier</option>
          <option value="hook">Sort: Hook score</option>
          <option value="views">Sort: Views</option>
          <option value="recent">Sort: Newest</option>
        </select>
        <select value={get("creator_id")} onChange={(e) => set("creator_id", e.target.value)}>
          <option value="">All creators</option>
          {creators.data?.map((c) => (
            <option key={c.id} value={c.id}>
              @{c.handle}
            </option>
          ))}
        </select>
        <select value={get("min_outlier")} onChange={(e) => set("min_outlier", e.target.value)}>
          <option value="">Any performance</option>
          <option value="1.5">1.5×+ median</option>
          <option value="3">3×+ (outliers)</option>
          <option value="10">10×+</option>
        </select>
        <select value={get("days")} onChange={(e) => set("days", e.target.value)}>
          <option value="">Any time</option>
          <option value="7">Last 7 days</option>
          <option value="30">Last 30 days</option>
          <option value="90">Last 90 days</option>
        </select>
        <select value={get("hook_type")} onChange={(e) => set("hook_type", e.target.value)}>
          <option value="">All hook types</option>
          {Object.entries(HOOK_TYPE_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <select value={get("analyzed")} onChange={(e) => set("analyzed", e.target.value)}>
          <option value="">Analyzed + not</option>
          <option value="true">Analyzed only</option>
          <option value="false">Not analyzed</option>
        </select>
      </div>
      <ErrorBox error={error} />
      {data && data.items.length === 0 && <Empty title="No posts match these filters" />}
      <div className="vgrid">{data?.items.map((v) => <VideoTile key={v.id} v={v} />)}</div>
    </div>
  );
}
