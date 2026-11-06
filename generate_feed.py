#!/usr/bin/env python3
"""
Hardened feed generator:
 - Reads YAML configs from FEEDS_DIR
 - Scrapes each site according to selectors
 - Writes one RSS/Atom XML per site into OUTPUT_DIR
 - Robust to missing/empty YAML keys and broken selectors
"""
import mimetypes
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import yaml
import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FEEDS_DIR = os.path.join(BASE_DIR, "feeds")
OUTPUT_DIR = os.path.join(BASE_DIR, "docs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

def safe_slug(text):
    if not text:
        return "site"
    s = re.sub(r"[^\w\-\.]+", "_", text.strip())
    return s[:120] or "site"

# def is_dynamic_site(url):
#     """
#     Optional check: You can expand this list with domains 
#     known to require heavy JS execution.
#     """
#     dynamic_indicators = ["lmu.de", "react", "angular", "vue"]
#     return any(indicator in url.lower() for indicator in dynamic_indicators)


# def fetch_html(url, timeout=20):
#     # 1. Quick attempt with requests (standard headers)
#     try:
#         headers = {
#             "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
#             "Accept-Language": "en-US,en;q=0.9",
#         }
#         r = requests.get(url, timeout=timeout//2, headers=headers)
#         r.raise_for_status()
#         # If the page returns a significant amount of HTML, assume it worked
#         if len(r.text) > 5000: 
#             return r.text
#     except Exception as e:
#         print(f"  [Static fetch skipped/failed] {url}")

#     # 2. Universal Stealth Fallback with Selenium
#     try:
#         chrome_options = Options()
#         # Use the modern headless engine
#         chrome_options.add_argument("--headless=new")
#         chrome_options.add_argument("--no-sandbox")
#         chrome_options.add_argument("--disable-dev-shm-usage")
        
#         # --- GLOBAL STEALTH FLAGS ---
#         # Masks the 'navigator.webdriver' flag and prevents detection
#         chrome_options.add_argument("--disable-blink-features=AutomationControlled")
#         chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
#         chrome_options.add_experimental_option('useAutomationExtension', False)
        
#         # Set a real-world User Agent
#         chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")

#         # Optimization: Don't wait for images/ads to load
#         chrome_options.page_load_strategy = 'eager'

#         driver = webdriver.Chrome(options=chrome_options)
#         driver.set_page_load_timeout(timeout)

#         try:
#             driver.get(url)
#             # Universal wait: Wait until the body is present AND 
#             # give JS a tiny window (2s) to execute its primary render.
#             WebDriverWait(driver, timeout).until(
#                 EC.presence_of_element_located((By.TAG_NAME, "body"))
#             )
#             import time
#             time.sleep(2) # Brief pause for AJAX/Table injection
#         except Exception:
#             # If it times out, we still proceed to grab what's currently in the DOM
#             pass

#         html = driver.page_source
#         driver.quit()
#         return html

#     except Exception as e:
#         print(f"  [Selenium Error] {url}: {e}")
#         if 'driver' in locals():
#             driver.quit()
#         return None
    
def fetch_html(url, timeout=20, allow_selenium=True):
    # Try requests first for faster static pages. Retry once so a transient
    # DNS/network blip does not silently drop a feed from the run.
    for attempt in range(2):
        try:
            r = requests.get(url, timeout=timeout, headers=HEADERS)
            r.raise_for_status()
            # Return bytes: BeautifulSoup then honours the page's real encoding
            # instead of requests defaulting to ISO-8859-1 when charset is absent.
            return r.content
        except Exception as e:
            print(f"  [fetch error with requests] {url}: {e}")
            if attempt == 0:
                time.sleep(2)

    if not allow_selenium:
        return None

    # Fallback to Selenium for JavaScript-heavy pages
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except ImportError:
        print(f"  [fetch error] Selenium not installed; cannot render {url}")
        return None

    driver = None
    try:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(timeout)
        driver.get(url)
        WebDriverWait(driver, timeout).until(EC.presence_of_all_elements_located((By.TAG_NAME, "body")))
        return driver.page_source
    except Exception as e:
        print(f"  [fetch error with Selenium] {url}: {e}")
        return None
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

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

def scrape_rss(url):
    """Read an existing RSS/Atom feed and return entries in scraper shape."""
    raw = fetch_html(url, allow_selenium=False)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"  [rss error] {url}: {e}")
        return []

    atom = "{http://www.w3.org/2005/Atom}"
    if root.tag == f"{atom}feed":
        nodes = root.findall(f"{atom}entry")
    elif root.tag == "rss":
        nodes = root.findall("./channel/item")
    else:
        nodes = list(root.iter("item"))

    entries = []
    for node in nodes:
        if root.tag == f"{atom}feed":
            title = (node.findtext(f"{atom}title") or "").strip()
            link_el = node.find(f"{atom}link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            desc = (node.findtext(f"{atom}summary") or node.findtext(f"{atom}content") or "").strip()
            pub = (node.findtext(f"{atom}updated") or node.findtext(f"{atom}published") or "").strip()
        else:
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            desc = (node.findtext("description") or "").strip()
            pub = (node.findtext("pubDate") or "").strip()

        if not title:
            continue
        entries.append({
            "title": title,
            "subtitle": "",
            "description": desc,
            "link": link or url,
            "picture": "",
            "link_is_fallback": not bool(link),
            "pub_date": parse_pubdate(pub),
        })

    entries.reverse()
    return entries

def scrape_site_from_cfg(cfg, fname_for_fallback):
    # defensively read config values
    site_name = (cfg.get("site_name") or "").strip() or safe_slug(fname_for_fallback)
    url = (cfg.get("url") or "").strip()
    if not url:
        print(f"Skipping {site_name}: no 'url' defined in config.")
        return site_name, []

    if (cfg.get("source_format") or "").strip().lower() == "rss":
        return site_name, scrape_rss(url)

    item_selector = (cfg.get("item_selector") or "").strip()
    if not item_selector:
        print(f"Skipping {site_name}: no 'item_selector' defined in config.")
        return site_name, []

    fields = cfg.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}
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
    seen = set()
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
                "subtitle": (fields.get("subtitle_is") or "") + safe_text(fields.get("subtitle", "")),
                "description": (fields.get("description_is") or "") + safe_text(fields.get("description", "")),
                "link": safe_link(fields.get("link", "")),
                "picture": safe_image(fields.get("picture", "")),
            }

            # minimum requirement: title present. Fall back to the text of the
            # first real link in the item; never the whole row (header junk).
            if not item["title"]:
                a = elem.find("a", href=True)
                if a:
                    item["title"] = a.get_text(" ", strip=True)
            if not item["title"]:
                continue

            # if link missing, fallback to the page url (better than nothing)
            if not item["link"]:
                item["link"] = url
                item["link_is_fallback"] = True
            else:
                item["link_is_fallback"] = False

            # de-duplicate: by normalized link, or by title for page-url fallbacks
            if item["link_is_fallback"]:
                key = " ".join(item["title"].split()).lower()
            else:
                key = item["link"].strip().lower().rstrip("/")
            if not key or key in seen:
                continue
            seen.add(key)

            entries.append(item)

        except Exception as e:
            print(f"  [item error] site={site_name} idx={i}: {e}")
            # continue to next item instead of aborting
            continue

    # Keep the same approach as before: reverse so first-detected becomes first in feed
    entries.reverse()
    return site_name, entries

def load_old_pubdates(out_path):
    """Map existing guid/link -> pubDate so re-runs keep stable dates."""
    mapping = {}
    if not os.path.isfile(out_path):
        return mapping
    try:
        root = ET.parse(out_path).getroot()
    except Exception as e:
        print(f"  [warn] could not read existing {out_path}: {e}")
        return mapping
    for item in root.iter("item"):
        pub = (item.findtext("pubDate") or "").strip()
        if not pub:
            continue
        for tag in ("guid", "link"):
            value = (item.findtext(tag) or "").strip()
            if value:
                mapping.setdefault(value, pub)
    return mapping

def parse_pubdate(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def build_and_write_feed(site_name, cfg, entries):
    if not entries:
        print(f"  No entries for {site_name}; skipping feed write.")
        return

    out_path = os.path.join(OUTPUT_DIR, safe_slug(site_name) + ".xml")
    old_pubdates = load_old_pubdates(out_path)

    fg = FeedGenerator()
    fg.title(site_name)
    fg.link(href=cfg.get("url") or "", rel="alternate")
    fg.description(cfg.get("description") or site_name)
    fg.language(cfg.get("language") or "en")

    for e in entries:
        fe = fg.add_entry()
        title = e.get("title", "")
        if e.get("subtitle"):
            title = f"{title} – {e.get('subtitle')}"
        fe.title(title)
        link = e.get("link") or ""
        fe.link(href=link)

        # Stable GUID: the item URL normally, the title when the link is only
        # the page fallback (so readers do not re-add every item on each run).
        is_fallback = e.get("link_is_fallback", False)
        guid_value = (e.get("title") or "") if is_fallback else link
        fe.guid(guid_value, permalink=not is_fallback)

        fe.description(e.get("description") or "")

        picture = e.get("picture") or ""
        if picture:
            fe.enclosure(
                url=picture,
                type=(mimetypes.guess_type(picture)[0] or "image/jpeg"),
                length="0",
            )

        # Prefer the source feed's own date, then the previous pubDate, then now().
        pub_date = e.get("pub_date") or parse_pubdate(
            old_pubdates.get(guid_value) or old_pubdates.get(link) or ""
        )
        fe.pubDate(pub_date or datetime.now(timezone.utc))

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
        try:
            site_name, entries = scrape_site_from_cfg(cfg, os.path.splitext(fname)[0])
            build_and_write_feed(site_name, cfg, entries)
        except Exception as e:
            print(f"Skipping {fname}: {type(e).__name__}: {e}")
            continue

if __name__ == "__main__":
    main()
