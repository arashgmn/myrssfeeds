#!/usr/bin/env python3
"""
Hardened feed generator:
 - Reads YAML configs from FEEDS_DIR
 - Scrapes each site according to selectors
 - Writes one RSS/Atom XML per site into OUTPUT_DIR
 - Robust to missing/empty YAML keys and broken selectors
"""
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import yaml
import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

FEEDS_DIR = "feeds"
OUTPUT_DIR = "docs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def safe_slug(text):
    if not text:
        return "site"
    s = re.sub(r"[^\w\-\.]+", "_", text.strip())
    return s[:120] or "site"

def fetch_html(url, timeout=20):
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  [fetch error] {url}: {e}")
        return None

def pick_image_src(element):
    # Try common attributes for images
    for attr in ("src", "data-src", "data-original", "data-lazy"):
        val = element.get(attr) if element else None
        if val:
            return val
    # fallback to srcset first candidate if present
    if element and element.has_attr("srcset"):
        ss = element["srcset"].split(",")[0].strip().split(" ")[0]
        if ss:
            return ss
    return ""

def normalize_href(href, base, prefix):
    if not href:
        return ""
    href = href.strip()
    # ignore javascript and fragments-only
    if href.startswith("javascript:") or href.startswith("mailto:") or href.startswith("#"):
        return ""
    # join relative links
    base_for_join = prefix or base
    try:
        return urljoin(base_for_join, href)
    except Exception:
        return ""

def scrape_site_from_cfg(cfg, fname_for_fallback):
    # defensively read config values
    site_name = (cfg.get("site_name") or "").strip() or safe_slug(fname_for_fallback)
    url = (cfg.get("url") or "").strip()
    if not url:
        print(f"Skipping {site_name}: no 'url' defined in config.")
        return site_name, []

    item_selector = (cfg.get("item_selector") or "").strip()
    if not item_selector:
        print(f"Skipping {site_name}: no 'item_selector' defined in config.")
        return site_name, []

    fields = cfg.get("fields") or {}
    link_prefix = (cfg.get("link_prefix") or "").strip() or None

    html = fetch_html(url)
    if not html:
        return site_name, []

    soup = BeautifulSoup(html, "html.parser")
    elements = []
    try:
        elements = soup.select(item_selector)
    except Exception as e:
        print(f"  [selector error] item_selector '{item_selector}' failed for {site_name}: {e}")
        return site_name, []

    entries = []
    for i, elem in enumerate(elements):
        try:
            # helper to safely extract text by selector
            def safe_text(sel):
                if not sel or not isinstance(sel, str) or not sel.strip():
                    return ""
                try:
                    el = elem.select_one(sel.strip())
                except Exception:
                    return ""
                return el.get_text(" ", strip=True) if el else ""

            # helper to safely extract link by selector (or fallback to first <a>)
            def safe_link(sel):
                href = ""
                if sel and isinstance(sel, str) and sel.strip():
                    try:
                        el = elem.select_one(sel.strip())
                    except Exception:
                        el = None
                    if el:
                        href = el.get("href") or ""
                # fallback: first <a> in element
                if not href:
                    a = elem.find("a")
                    if a:
                        href = a.get("href") or ""
                # finally normalize
                return normalize_href(href, url, link_prefix)

            # helper to find images
            def safe_image(sel):
                img_src = ""
                if sel and isinstance(sel, str) and sel.strip():
                    try:
                        el = elem.select_one(sel.strip())
                    except Exception:
                        el = None
                    if el:
                        img_src = pick_image_src(el) or ""
                if not img_src:
                    img = elem.find("img")
                    if img:
                        img_src = pick_image_src(img) or ""
                if not img_src:
                    return ""
                return urljoin(link_prefix or url, img_src)

            item = {
                "title": safe_text(fields.get("title", "")),
                "subtitle": fields.get("subtitle_is", "") + safe_text(fields.get("subtitle", "")),
                "description": fields.get("description_is", "") + safe_text(fields.get("description", "")),
                "link": safe_link(fields.get("link", "")),
                "picture": safe_image(fields.get("picture", "")),
            }

            # minimum requirement: title present
            if not item["title"]:
                # skip items with no title (too noisy)
                continue

            # if link missing, fallback to the page url (better than nothing)
            if not item["link"]:
                item["link"] = url

            entries.append(item)

        except Exception as e:
            print(f"  [item error] site={site_name} idx={i}: {e}")
            # continue to next item instead of aborting
            continue

    # Keep the same approach as before: reverse so first-detected becomes first in feed
    entries.reverse()
    return site_name, entries

def build_and_write_feed(site_name, cfg, entries):
    if not entries:
        print(f"  No entries for {site_name}; skipping feed write.")
        return

    fg = FeedGenerator()
    fg.title(site_name)
    fg.link(href=cfg.get("url", ""), rel="alternate")
    fg.description(cfg.get("description", f"{site_name}"))
    fg.language(cfg.get("language", "en"))

    for e in entries:
        fe = fg.add_entry()
        title = e.get("title", "")
        if e.get("subtitle"):
            title = f"{title} – {e.get('subtitle')}"
        fe.title(title)
        fe.link(href=e.get("link", ""))
        desc = e.get("description", "") or ""
        if e.get("picture"):
            desc = f'<img src="{e.get("picture")}" alt="image" /><br/>{desc}'
        fe.description(desc)
        fe.pubDate(datetime.now(timezone.utc))

    fname = safe_slug(site_name) + ".xml"
    out_path = os.path.join(OUTPUT_DIR, fname)
    try:
        fg.rss_file(out_path)
        print(f"  Wrote {out_path} ({len(entries)} items)")
    except Exception as e:
        print(f"  [write error] Could not write {out_path}: {e}")

def main():
    if not os.path.isdir(FEEDS_DIR):
        print(f"Feeds directory '{FEEDS_DIR}' does not exist.")
        return

    files = sorted(os.listdir(FEEDS_DIR))
    for fname in files:
        if not fname.lower().endswith((".yml", ".yaml")):
            continue
        path = os.path.join(FEEDS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        except Exception as e:
            print(f"Skipping {fname}: failed to parse YAML: {e}")
            continue
        
        # print('filename:', fname)
        site_name, entries = scrape_site_from_cfg(cfg, os.path.splitext(fname)[0])
        build_and_write_feed(site_name, cfg, entries)

if __name__ == "__main__":
    main()
