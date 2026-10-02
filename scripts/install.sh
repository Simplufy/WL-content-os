#!/usr/bin/env bash
# One-shot install on a Linux box (Ubuntu/Debian tested). Re-runnable.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
PORT="${STUDIO_PORT:-8794}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1 — $2"; exit 1; }; }
need ffmpeg  "install ffmpeg (with libass; libplacebo/zscale recommended for HDR footage)"
need node    "install Node.js 22+"
need npm     "install Node.js 22+"
need uv      "install uv: https://docs.astral.sh/uv/"
need claude  "install the Claude Code CLI, then run 'claude setup-token' and put the token in .env"

echo "→ Python environment"
uv venv --python 3.14 .venv 2>/dev/null || true
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m playwright install chromium

echo "→ yt-dlp (with JS runtime support for YouTube)"
uv tool install --force 'yt-dlp[default]'

echo "→ Dashboard"
(cd web && npm ci && npm run build)

echo "→ Config"
[ -f .env ] || cp .env.example .env
[ -f brand.json ] || cp brand.example.json brand.json

echo "→ systemd user services"
mkdir -p ~/.config/systemd/user
for u in content-studio content-studio-worker; do
  sed -e "s|__INSTALL_DIR__|$DIR|g" -e "s|__PORT__|$PORT|g" -e "s|__HOME__|$HOME|g" "deploy/$u.service" > ~/.config/systemd/user/$u.service
done
systemctl --user daemon-reload
systemctl --user enable --now content-studio content-studio-worker

echo
echo "Done. Open http://127.0.0.1:$PORT"
echo "Next: edit brand.json and .env, install whisper.cpp + a model (see README), then restart:"
echo "  systemctl --user restart content-studio content-studio-worker"
