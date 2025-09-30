#!/bin/bash
eval "$(conda shell.bash hook)"
conda activate
cd $HOME/myrssfeeds
python generate_feed.py
git add .
git commit -m "Auto update $(date -u +%F_%T)" || exit 0
git push origin main
