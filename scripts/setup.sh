#!/usr/bin/env bash
# Idempotent environment setup for Claude Code Cloud (also usable as the environment setup script).
set -euo pipefail
cd "$(dirname "$0")/.."

need_apt=()
command -v ffmpeg >/dev/null || need_apt+=(ffmpeg)
command -v espeak-ng >/dev/null || need_apt+=(espeak-ng)
command -v tesseract >/dev/null || need_apt+=(tesseract-ocr tesseract-ocr-kor)
fc-list :lang=ko | grep -qi "noto sans cjk" || need_apt+=(fonts-noto-cjk)
if [ ${#need_apt[@]} -gt 0 ]; then
  (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${need_apt[@]}") \
    || echo "WARN: apt install failed for: ${need_apt[*]}" >&2
fi

[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -e '.[dev,cloud]' || {
  echo "WARN: optional cloud extras (boto3, anthropic) unavailable; installing core only" >&2
  .venv/bin/pip install -q -e '.[dev]'
}

(cd render && npm ci --silent)

# Remotion needs a Chromium; Claude Code Cloud images ship one under /opt/pw-browsers.
if [ -z "${REMOTION_BROWSER_EXECUTABLE:-}" ]; then
  cand=$(ls -d /opt/pw-browsers/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | head -1 || true)
  [ -n "$cand" ] && echo "export REMOTION_BROWSER_EXECUTABLE=$cand"
fi
echo "setup complete — run: .venv/bin/dgl status"
