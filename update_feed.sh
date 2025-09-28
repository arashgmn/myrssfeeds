#!/bin/bash
cd /home/arash/myrssfeeds
/home/arash/myrssfeeds/generate_feed.py
git add feed.xml
git commit -m "Auto update $(date -u +%F_%T)" || exit 0
git push origin main
