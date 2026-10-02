export type Platform = "tiktok" | "youtube" | "instagram";

export interface HookBrief {
  spoken?: string;
  onscreen_text?: string;
  type?: string;
  template?: string;
}

export interface VideoCard {
  id: number;
  creator_id: number;
  platform: Platform;
  url: string;
  title: string | null;
  published_at: string | null;
  duration: number | null;
  thumbnail_url: string | null;
  views: number | null;
  likes: number | null;
  comments: number | null;
  shares: number | null;
  saves: number | null;
  is_new: number;
  status: string;
  error: string | null;
  outlier: number | null;
  engagement_rate: number | null;
  engagement_rel: number | null;
  hook_score: number | null;
  score: number | null;
  handle: string;
  display_name: string | null;
  avatar_url: string | null;
  has_media: boolean;
  age_hours: number | null;
  early: boolean;
  hook: HookBrief | null;
  topic: string | null;
  format: string | null;
  poster: string | null;
}

export interface Analysis {
  hook: HookBrief & {
    visual: string;
    score: number;
    subscores: Record<string, number>;
    why: string;
  };
  format: string;
  topic: string;
  angle: string;
  beats: { start: number; label: string; summary: string }[];
  cta: string;
  retention_tactics: string[];
  summary: string;
  brand_angle: string;
  relevance: number;
}

export interface Baseline {
  median_views: number | null;
  median_er: number | null;
  n: number;
}

export interface VideoDetail extends VideoCard {
  description: string | null;
  transcript_text: string | null;
  transcript: { start: number; end: number; text: string }[];
  analysis: Analysis | null;
  frames: { t: number; hook: boolean; url: string }[];
  snapshots: { at: string; views: number | null; likes: number | null; comments: number | null }[];
  baseline: Baseline;
}

export interface Creator {
  id: number;
  platform: Platform;
  handle: string;
  profile_url: string;
  display_name: string | null;
  avatar_url: string | null;
  followers: number | null;
  notes: string | null;
  active: number;
  added_at: string;
  last_checked_at: string | null;
  last_check_status: string | null;
  last_check_error: string | null;
  posts?: number;
  analyzed?: number;
  avg_score?: number | null;
  best_outlier?: number | null;
  last_post?: string | null;
  processing?: number;
  check_pending?: boolean;
  baseline: Baseline;
  videos?: VideoCard[];
}

export interface LlmStatus {
  ok: boolean | null;
  detail: string;
  at: string | null;
}

export interface Dashboard {
  kpis: Record<string, number>;
  newest: VideoCard[];
  outliers: VideoCard[];
  top_hooks: VideoCard[];
  events: EventRow[];
  llm: LlmStatus;
  processing: { id: number; status: string; title: string | null; handle: string }[];
}

export interface EventRow {
  id: number;
  at: string;
  level: "info" | "warn" | "error";
  message: string;
  creator_id: number | null;
  video_id: number | null;
}

export interface HookRow {
  id: number;
  url: string;
  views: number | null;
  outlier: number | null;
  hook_score: number | null;
  score: number | null;
  published_at: string | null;
  handle: string;
  platform: Platform;
  hook: Analysis["hook"];
  topic: string;
  format: string;
}

export interface Settings {
  brand_brief: string;
  brand_brief_is_default: boolean;
  cookies: Record<Platform, boolean>;
  llm: LlmStatus;
  llm_model: string;
  whisper_model: string | null;
  check_interval_min: number;
  content_pillars: string[];
  auto_ideas: boolean;
  new_posts_only: boolean;
  auto_styles: boolean;
  backfill_top: number;
  backfill_newest: number;
}

export interface Job {
  id: number;
  kind: string;
  ref_id: number | null;
  status: string;
  priority: number;
  attempts: number;
  max_attempts: number;
  run_after: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface IdeaHook {
  text: string;
  onscreen_text: string;
  type: string;
  source_video_id: number | null;
}

export interface ScriptLine {
  id: string;
  section: string;
  text: string;
  onscreen_text: string;
  broll: string;
}

export interface Script {
  hook: { spoken: string; onscreen_text: string; visual: string };
  lines: ScriptLine[];
  caption: string;
  hashtags: string[];
  duration_s: number;
  filming_notes: string;
  word_count?: number;
}

export interface Originality {
  ok: boolean;
  matches: { line: string; phrase: string; video_id: number; handle: string }[];
  checked_against: number;
  rules_ok?: boolean;
  rule_flags?: { where: string; term: string; why: string }[];
}

export type GenStatus = "none" | "pending" | "ready" | "failed" | "waiting_llm";

export interface Idea {
  id: number;
  batch_id: number | null;
  title: string;
  pillar: string | null;
  format: string | null;
  angle: string | null;
  mechanism: string | null;
  funnel: "TOFU" | "MOFU" | "BOFU" | null;
  why: string | null;
  hooks: IdeaHook[];
  chosen_hook: number | null;
  inspired_by: number[];
  stage: string;
  script_status: GenStatus;
  script_error: string | null;
  script_version: number;
  hooks_status: GenStatus;
  originality: Originality | null;
  has_script: boolean;
  script_summary: { duration_s: number; word_count: number; lines: number } | null;
  notes: string | null;
  starred: number;
  archived: number;
  created_at: string;
  updated_at: string;
  script?: Script | null;
  inspired_videos?: VideoCard[];
}

export interface IdeaBatch {
  id: number;
  focus: string | null;
  count: number;
  source_video_ids: number[];
  auto: number;
  status: GenStatus;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  ideas: number;
}

export interface IdeasList {
  items: Idea[];
  batches: IdeaBatch[];
  stages: string[];
  pillars: string[];
  library_size: number;
}

export interface EditOptions {
  captions: boolean;
  caption_style: "brand" | "bold" | "clean" | "impact";
  graphics: boolean;
  callouts: boolean;
  style_id?: number | null;
  hook_title: boolean;
  zooms: boolean;
  max_pause: number;
}

export interface EditProject {
  id: number;
  title: string;
  idea_id: number | null;
  source_name: string | null;
  status: "queued" | "preparing" | "deciding" | "rendering" | "ready" | "failed" | "waiting_llm";
  stage: string | null;
  progress: number | null;
  error: string | null;
  instruction: string | null;
  target_s: number | null;
  render_count: number;
  created_at: string;
  updated_at: string;
  raw_duration: number | null;
  duration: number | null;
  has_output: boolean;
  thumb: string | null;
  options: EditOptions;
  hook_text: string | null;
  // full
  notes?: string | null;
  callouts?: { text: string; color: string; start: number; end: number }[];
  graphics?: { type: string; label?: string; start: number; end: number }[];
  segments?: number;
  overrides?: { restore: number[]; remove: number[] };
  words?: { i: number; w: string; s: number; e: number; k: boolean; r: string | null }[];
  reordered?: boolean;
  probe?: { duration: number; width: number; height: number; portrait: boolean; hdr: boolean } | null;
}

export interface Integration {
  id: string;
  name: string;
  provider: string;
  label: string;
  picture: string | null;
  disabled: boolean;
  profile: string | null;
}

export interface PublishStatus {
  configured: boolean;
  base_url: string;
  ui_url: string;
  public: boolean;
  connected: boolean;
  integrations: Integration[];
  error: string | null;
}

export interface RuleFlag {
  where: string;
  term: string;
  why: string;
}

export interface Publication {
  id: number;
  edit_id: number;
  idea_id: number | null;
  kind: "schedule" | "draft" | "now";
  status: "submitting" | "scheduled" | "publishing" | "draft" | "published" | "error" | "cancelled";
  scheduled_at: string | null;
  published_at: string | null;
  error: string | null;
  created_at: string;
  title: string | null;
  thumb: string | null;
  channels: { integration_id: string; provider: string; name: string; picture: string | null; title: string; text: string }[];
  results: { id: string; state: string; url: string | null; provider: string; name: string }[];
  stale_render: boolean;
}

export interface Me {
  user: { id: number; email: string; name: string | null; is_admin: number } | null;
  local: boolean;
  has_users: boolean;
}

export interface TeamUser {
  id: number;
  email: string;
  name: string | null;
  is_admin: number;
  created_at: string;
  last_login_at: string | null;
}

export interface EditStyle {
  id: number;
  name: string;
  description: string | null;
  best_for: string | null;
  source: "builtin" | "library" | "custom";
  params: Record<string, string | number | boolean>;
  preview_status: string;
  preview_error: string | null;
  gif: string | null;
  mp4: string | null;
  inspired: VideoCard[];
  created_at: string;
}

export interface StylesList {
  items: EditStyle[];
  status: { state: string; remaining?: number; styles?: number; error?: string; at?: string };
  fingerprinted: number;
  analyzed: number;
  last_build: { at: string; fingerprints: number; added?: number; updated?: number; retired?: number; changelog?: string } | null;
  auto: boolean;
  retired: { id: number; name: string; retire_reason: string | null; updated_at: string }[];
  knobs: Record<string, (string | number | boolean)[]>;
}

export interface Brand {
  product_name: string;
  org_name: string;
  logo_initials: string;
  public_url: string;
  colors: Record<string, string>;
  fonts: Record<string, string>;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  if (res.status === 401 && !path.startsWith("/api/auth/") && location.pathname !== "/login") {
    location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const detail = data?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : `Request failed (${res.status})`);
  }
  return data as T;
}

export const api = {
  dashboard: () => req<Dashboard>("GET", "/api/dashboard"),
  creators: () => req<Creator[]>("GET", "/api/creators"),
  creator: (id: number) => req<Creator>("GET", `/api/creators/${id}`),
  addCreator: (url: string, notes?: string) => req<Creator & { warning: string | null }>("POST", "/api/creators", { url, notes }),
  patchCreator: (id: number, body: Partial<{ active: boolean; notes: string; check_interval_min: number }>) =>
    req<Creator>("PATCH", `/api/creators/${id}`, body),
  deleteCreator: (id: number) => req("DELETE", `/api/creators/${id}`),
  checkCreator: (id: number) => req("POST", `/api/creators/${id}/check`),
  videos: (params: Record<string, string | number | boolean | undefined>) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => v !== undefined && v !== "" && q.set(k, String(v)));
    return req<{ total: number; items: VideoCard[] }>("GET", `/api/videos?${q}`);
  },
  video: (id: number) => req<VideoDetail>("GET", `/api/videos/${id}`),
  processVideo: (id: number) => req("POST", `/api/videos/${id}/process`),
  analyzeVideo: (id: number) => req("POST", `/api/videos/${id}/analyze`),
  hooks: (sort: string, hookType?: string) =>
    req<HookRow[]>("GET", `/api/hooks?sort=${sort}${hookType ? `&hook_type=${hookType}` : ""}`),
  settings: () => req<Settings>("GET", "/api/settings"),
  ideas: (archived = false) => req<IdeasList>("GET", `/api/ideas${archived ? "?archived=true" : ""}`),
  idea: (id: number) => req<Idea>("GET", `/api/ideas/${id}`),
  generateIdeas: (body: { count: number; focus?: string; video_ids?: number[] }) =>
    req<{ batch_id: number }>("POST", "/api/ideas/generate", body),
  createIdea: (title: string, angle?: string) => req<Idea>("POST", "/api/ideas", { title, angle }),
  patchIdea: (id: number, body: Partial<{ title: string; angle: string; pillar: string; format: string; stage: string; notes: string; starred: boolean; archived: boolean; chosen_hook: number }>) =>
    req<Idea>("PATCH", `/api/ideas/${id}`, body),
  deleteIdea: (id: number) => req("DELETE", `/api/ideas/${id}`),
  writeScript: (id: number, instruction?: string) => req("POST", `/api/ideas/${id}/script`, { instruction }),
  saveScript: (id: number, script: Script) => req<Idea>("PUT", `/api/ideas/${id}/script`, { script }),
  moreHooks: (id: number, instruction?: string) => req("POST", `/api/ideas/${id}/hooks`, { instruction }),
  edits: () => req<EditProject[]>("GET", "/api/edits"),
  edit: (id: number) => req<EditProject>("GET", `/api/edits/${id}`),
  importEdit: (body: { path: string; title?: string; idea_id?: number; instruction?: string; target_s?: number; style_id?: number }) =>
    req<EditProject>("POST", "/api/edits/import", body),
  // Chunked: Cloudflare caps one request at 100 MB, raw footage is often 1 GB+.
  uploadEdit: async (file: File, params: { title?: string; idea_id?: number; instruction?: string; style_id?: number }, onProgress?: (f: number) => void) => {
    const init = await req<{ id: string; chunk_size: number }>("POST", "/api/uploads", { filename: file.name, size: file.size });
    let offset = 0;
    while (offset < file.size) {
      const end = Math.min(file.size, offset + init.chunk_size);
      let tries = 0;
      for (;;) {
        const res = await fetch(`/api/uploads/${init.id}?offset=${offset}`, { method: "PUT", body: file.slice(offset, end) });
        if (res.ok) break;
        if (++tries >= 4) throw new ApiError(res.status, `Upload failed at ${Math.round((offset / file.size) * 100)}% (${res.status})`);
        await new Promise((r) => setTimeout(r, 1500 * tries));
      }
      offset = end;
      onProgress?.(offset / file.size);
    }
    return req<EditProject>("POST", `/api/uploads/${init.id}/complete`, {
      title: params.title || file.name.replace(/\.[^.]+$/, ""), idea_id: params.idea_id, instruction: params.instruction, style_id: params.style_id,
    });
  },
  patchEdit: (id: number, body: Partial<{ title: string; idea_id: number; options: Partial<EditOptions> }>) =>
    req<EditProject>("PATCH", `/api/edits/${id}`, body),
  setOverrides: (id: number, body: { restore: number[]; remove: number[] }) => req<EditProject>("POST", `/api/edits/${id}/overrides`, body),
  renderEdit: (id: number) => req("POST", `/api/edits/${id}/render`),
  redecideEdit: (id: number, instruction?: string, target_s?: number) => req("POST", `/api/edits/${id}/redecide`, { instruction, target_s }),
  deleteEdit: (id: number) => req("DELETE", `/api/edits/${id}`),
  publishStatus: () => req<PublishStatus>("GET", "/api/publish/status"),
  publishCaptions: (edit_id: number, providers: string[], instruction?: string) =>
    req<{ captions: { provider: string; title: string; text: string; flags: RuleFlag[] }[] }>("POST", "/api/publish/captions", { edit_id, providers, instruction }),
  publishCheck: (text: string, title?: string) => req<{ flags: RuleFlag[] }>("POST", "/api/publish/check", { text, title }),
  publishSlot: (integrationId: string) => req<{ date: string | null }>("GET", `/api/publish/slot/${integrationId}`),
  publications: (editId?: number) => req<Publication[]>("GET", `/api/publications${editId ? `?edit_id=${editId}` : ""}`),
  createPublication: (body: { edit_id: number; channels: Record<string, unknown>[]; when: string; date?: string | null; override_rules?: boolean }) =>
    req<Publication>("POST", "/api/publications", body),
  cancelPublication: (id: number) => req<Publication>("POST", `/api/publications/${id}/cancel`),
  retryPublication: (id: number) => req("POST", `/api/publications/${id}/retry`),
  syncPublications: () => req("POST", "/api/publish/sync"),
  brand: () => req<Brand>("GET", "/api/brand"),
  me: () => req<Me>("GET", "/api/auth/me"),
  login: (email: string, password: string) => req<{ user: Me["user"] }>("POST", "/api/auth/login", { email, password }),
  logout: () => req("POST", "/api/auth/logout"),
  teamUsers: () => req<TeamUser[]>("GET", "/api/auth/users"),
  addTeamUser: (body: { email: string; password: string; name?: string; is_admin?: boolean }) => req("POST", "/api/auth/users", body),
  removeTeamUser: (id: number) => req("DELETE", `/api/auth/users/${id}`),
  changePassword: (password: string) => req("POST", "/api/auth/password", { password }),
  styles: () => req<StylesList>("GET", "/api/styles"),
  buildStyles: () => req("POST", "/api/styles/build"),
  patchStyle: (id: number, body: Partial<{ name: string; params: Record<string, unknown>; archived: boolean }>) => req<EditStyle>("PATCH", `/api/styles/${id}`, body),
  rerenderStylePreview: (id: number) => req("POST", `/api/styles/${id}/preview`),
  setEditStyle: (editId: number, style_id: number) => req<EditProject>("POST", `/api/edits/${editId}/style`, { style_id }),
  saveSettings: (body: Partial<{ brand_brief: string; content_pillars: string[]; auto_ideas: boolean; auto_styles: boolean; new_posts_only: boolean; backfill_top: number; backfill_newest: number }>) => req<Settings>("PUT", "/api/settings", body),
  uploadCookies: (platform: Platform, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return req<Settings>("POST", `/api/settings/cookies/${platform}`, fd);
  },
  deleteCookies: (platform: Platform) => req<Settings>("DELETE", `/api/settings/cookies/${platform}`),
  pingLlm: () => req<LlmStatus>("POST", "/api/llm/ping"),
  jobs: (status?: string) => req<{ counts: Record<string, number>; items: Job[] }>("GET", `/api/jobs${status ? `?status=${status}` : ""}`),
  retryJob: (id: number) => req("POST", `/api/jobs/${id}/retry`),
  events: () => req<EventRow[]>("GET", "/api/events"),
};
