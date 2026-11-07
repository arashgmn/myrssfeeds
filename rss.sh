#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate
python generate_feed.py || echo "warning: generate_feed reported an error; continuing"
# Validation is advisory: a site with no openings, or a broken scrape, must not
# block the run. Real breakage is visible in the summary line.
python validate_feeds.py || echo "warning: feed validation reported problems; continuing"
git add .
git commit -m "Auto update $(date -u +%F_%T)" || exit 0
git push origin main
