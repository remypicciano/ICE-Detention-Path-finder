#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Create it and install requirements first."
  exit 1
fi

.venv/bin/python -m PyInstaller --noconfirm --clean ICEDetentionPathway.spec

echo "Build complete: dist/ICEDetentionPathway"
