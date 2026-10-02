# Content Studio (white-label Content OS)

A self-hosted content operating system for short-form video:
**watch creators → learn what works → write → edit raw footage → publish.**
Rebrand it for any client with one `brand.json`.

| Module | What it does |
|---|---|
| **Creators** | Paste TikTok / YouTube / Instagram profile links. Studio backfills their best + newest posts, then monitors for new uploads (every 3h by default). |
| **Videos & scoring** | Each post is downloaded, transcribed with word timings, and torn down by Claude: hook (spoken / on-screen / visual), hook type & 0-100 score, structure, CTA, reusable template. Outlier score = views vs the creator's median. |
| **Hooks** | Library of every analyzed opener with its reusable pattern. |
| **Ideas & Scripts** | Claude turns the scored library + your brand brief into concepts (pillar, mechanism, funnel stage), hook options and recordable scripts; originality + brand-rule checks; teleprompter. |
| **Editor** | Upload raw footage (bad takes, silences, HDR iPhone, 4K). Claude picks the takes word-by-word; a listen-back QA loop removes leftover stutters; face-aware reframing and punch-ins; captions; branded motion graphics reviewed by Claude on a contact sheet; synthesized UI sound; -14 LUFS audio. |
| **Styles** | Editing styles learned from the best videos in your library (cut pace, captions, zooms, graphics density, grade), merged with your brand, each with a 7s GIF preview. Keeps evolving as more videos are analyzed. |
| **Publish** | Schedule finished edits to every channel through a self-hosted [Postiz](https://postiz.com): per-platform captions, brand-rule gate, calendar, status sync. |

## Requirements
- Linux box (tested on Ubuntu, 32-core CPU; a GPU helps whisper.cpp but isn't required)
- Python 3.14 (installed by `uv`), Node.js 22+, `ffmpeg` with libass (libplacebo/zscale recommended for HDR)
- [Claude Code CLI](https://claude.com/claude-code) logged in to a Claude subscription (`claude setup-token`)
- [whisper.cpp](https://github.com/ggml-org/whisper.cpp) built in `~/whisper.cpp` with a model in `~/whisper.cpp/models/`
  (large-v3-turbo recommended; base.en works) — used for competitor transcripts. The editor uses faster-whisper.
- Optional: Postiz (Docker) for publishing; Cloudflare Tunnel for a public HTTPS address

## Install
```bash
git clone <this repo> content-studio && cd content-studio
./scripts/install.sh
```
Then:
1. Edit `brand.json` (see [docs/WHITELABEL.md](docs/WHITELABEL.md)) and `.env`.
2. Open http://127.0.0.1:8794 → Settings: test the Claude connection, paste the full brand brief,
   set pillars, add the first team account (Settings → Team, from the machine itself).
3. Add creators. The first teardowns land within minutes.

## Layout
```
studio/            FastAPI API, worker, all pipelines
  brand.py         white-label config (defaults + brand.json)
  editor/          raw-footage editor (decide, plan, qa, render, captions, motion graphics, critique, sfx)
web/               React dashboard (Vite) — `cd web && npm run build`
deploy/            systemd user unit templates (installed by scripts/install.sh)
docs/              WHITELABEL.md, POSTIZ.md
tests/             pytest — `.venv/bin/python -m pytest tests`
```

## Operations
```bash
systemctl --user status content-studio content-studio-worker
journalctl --user -u content-studio-worker -f
```
Data lives in `data/studio/studio.db` (SQLite) and `media/` (downloads, edits, previews); both are gitignored.

## Public access
Put the dashboard behind HTTPS (e.g. a Cloudflare Tunnel to `localhost:8794`). Requests through a proxy
must sign in (team accounts, Settings → Team); requests made directly on the machine skip sign-in.
Uploads are chunked so they pass proxies with request-size limits (Cloudflare: 100 MB).
