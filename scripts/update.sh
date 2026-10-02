#!/usr/bin/env bash
# Pull the latest Content Studio and redeploy this install. Instance files (brand.json, .env, data/, media/,
# local/) are gitignored and never touched.
set -euo pipefail
cd "$(dirname "$0")/.."
PREFIX="${STUDIO_SERVICE_PREFIX:-content-studio}"
git pull --ff-only
uv pip install --python .venv/bin/python -q -r requirements.txt
(cd web && npm ci --silent && npm run build)
.venv/bin/python -m pytest tests -q
systemctl --user restart "$PREFIX" "$PREFIX-worker"
echo "Updated to $(git log --oneline -1)"
