#!/usr/bin/env python3
import os
import datetime
import yaml
import requests
from datetime import datetime
from dateutil.tz import tzlocal
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from urllib.parse import urljoin

FEEDS_DIR = 'feeds'
OUTPUT_FILE = 'feed.xml'

def scrape_site(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    site_name = cfg['site_name']
    url = cfg['url']
    item_selector = cfg['item_selector']
    fields = cfg['fields']
    link_prefix = cfg.get('link_prefix', '')

    print(f"Scraping {site_name} ...")
    print(f'\t items: {item_selector}')
    print(f'\t fields: {fields}')
    print(f'\t link_prefix: {link_prefix}')
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return []

    soup = BeautifulSoup(r.text, 'html.parser')
    
    items = []
    for elem in soup.select(item_selector):
        def safe_text(selector):
            el = elem.select_one(selector)
            return el.get_text(strip=True) if el else ''

        def safe_link(selector):
            el = elem.select_one(selector)
            if not el: return ''
            href = el.get('href', '')
            return urljoin(link_prefix or url, href)

        item = {
            'site': site_name,
            'title': safe_text(fields.get('title', '')),
            'subtitle': safe_text(fields.get('subtitle', '')),
            'description': safe_text(fields.get('description', '')),
            'link': safe_link(fields.get('link', ''))
        }
        # Skip if no title/link
        if item['title'] and item['link']:
            items.append(item)

    return items

def main():
    fg = FeedGenerator()
    fg.title('My Aggregated Feed')
    fg.link(href='https://arashgmn.github.io/myrssfeeds/', rel='alternate')
    fg.description('Aggregated feed generated from custom YAML configs')
    fg.language('en')

    all_items = []
    for fname in os.listdir(FEEDS_DIR):
        if fname.endswith(('.yaml', '.yml')):
            all_items.extend(scrape_site(os.path.join(FEEDS_DIR, fname)))

    for item in all_items:
        fe = fg.add_entry()
        # Combine title + subtitle for clarity
        title = item['title']
        if item['subtitle']:
            title += f" – {item['subtitle']}"
        fe.title(title)
        fe.link(href=item['link'])
        # Description can contain HTML
        fe.description(item['description'])
        fe.author({'name': item['site']})
        fe.pubDate(datetime.now(tzlocal()))
        # fe.pubDate(datetime.now(timezone.utc))
        # fe.pubDate(datetime.datetime.utcnow())

    fg.rss_file(OUTPUT_FILE)
    print(f"Generated {OUTPUT_FILE} with {len(all_items)} items.")

if __name__ == "__main__":
    main()

