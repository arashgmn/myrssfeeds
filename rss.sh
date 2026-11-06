#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate
python generate_feed.py
# Abort before commit/push if generated feeds are missing or broken.
python validate_feeds.py
git add .
git commit -m "Auto update $(date -u +%F_%T)" || exit 0
git push origin main
