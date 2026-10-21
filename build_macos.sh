#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Create it and install requirements first."
  exit 1
fi

.venv/bin/python -m PyInstaller --noconfirm --clean ICEDetentionPathway.spec

# The dataset URLs ship inside the app as well as beside it, so a copy that is
# moved away from its folder still finds them.
cp data-sources.json dist/data-sources.json

# A bundle whose icon macOS cannot read is shown with a placeholder of its
# own, so the icon is checked rather than assumed.
.venv/bin/python tools/verify_bundle_icon.py dist/ICEDetentionPathway.app

echo "Build complete: dist/ICEDetentionPathway.app"
