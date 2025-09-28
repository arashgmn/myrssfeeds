#!/usr/bin/env python3
from feedgen.feed import FeedGenerator
import requests, datetime
from bs4 import BeautifulSoup

URLS = [
    "https://example.com/news",
    # add more URLs
]

fg = FeedGenerator()
fg.title('My Scraped Feed')
fg.link(href='https://example.com', rel='alternate')
fg.description('Daily scraped updates')
fg.language('en')

for u in URLS:
    r = requests.get(u)
    soup = BeautifulSoup(r.text, 'html.parser')
    # ***Adjust parsing logic to your sites***
    item_title = soup.find('h1').text.strip()
    item_link  = u
    item_desc  = soup.find('p').text.strip()
    fe = fg.add_entry()
    fe.title(item_title)
    fe.link(href=item_link)
    fe.description(item_desc)
    fe.pubDate(datetime.datetime.utcnow())

fg.rss_file('feed.xml')
