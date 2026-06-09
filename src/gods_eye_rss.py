"""
gods_eye_rss.py
RSS / news monitor for God's Eye.
Watches feeds relevant to Hormuz, BoJ, FX intervention, repatriation, SPR.
Outputs: data/rss_alerts.csv
"""
import re
import csv
import os
import requests
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from dataclasses import dataclass, field

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "rss_alerts.csv")

FEEDS = [
    {"name": "Reuters Energy",      "url": "https://feeds.reuters.com/reuters/energyNews"},
    {"name": "Bloomberg Energy",    "url": "https://feeds.bloomberg.com/energy/news.rss"},
    {"name": "EIA Today in Energy", "url": "https://www.eia.gov/rss/todayinenergy.xml"},
    {"name": "Reuters Japan",       "url": "https://feeds.reuters.com/reuters/JPBusinessNews"},
    {"name": "Nikkei Asia RSS",     "url": "https://asia.nikkei.com/rss/feed/nar"},
    {"name": "WSJ Economy",         "url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"},
    {"name": "FT Markets",          "url": "https://www.ft.com/markets?format=rss"},
]

KEYWORD_GROUPS = {
    "HORMUZ":       [r"hormuz", r"strait", r"iran.{0,20}oil", r"oil.{0,20}supply", r"tanker"],
    "BOJ":          [r"bank of japan", r"boj", r"ueda", r"japan.{0,20}rate", r"yen.{0,20}hike"],
    "FX_DEFENSE":   [r"usd.{0,5}jpy", r"yen.{0,10}intervention", r"mof.{0,20}yen", r"currency.{0,10}defense"],
    "REPATRIATION": [r"japan.{0,20}treasur", r"jgb.{0,20}sell", r"repatri", r"japan.{0,20}reserve"],
    "SPR":          [r"\bspr\b", r"strategic.{0,10}petroleum", r"iea.{0,10}release"],
}


@dataclass
class Alert:
    feed:      str
    title:     str
    link:      str
    published: str
    tags:      list = field(default_factory=list)
    snippet:   str = ""


def fetch_feed(url: str, timeout: int = 15):
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "GodsEye/1.0"})
        resp.raise_for_status()
        return ET.fromstring(resp.content)
    except Exception as e:
        print(f"  [rss] WARN: could not fetch {url}: {e}")
        return None


def match_keywords(text: str) -> list:
    text_lower = text.lower()
    tags = []
    for tag, patterns in KEYWORD_GROUPS.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                tags.append(tag)
                break
    return tags


def parse_feed(root, feed_name: str) -> list:
    alerts = []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    for item in items:
        title = (item.findtext("title") or item.findtext("atom:title", namespaces=ns) or "").strip()
        link  = (item.findtext("link")  or item.findtext("atom:link",  namespaces=ns) or "").strip()
        desc  = (item.findtext("description") or item.findtext("atom:summary", namespaces=ns) or "").strip()
        pub   = (item.findtext("pubDate") or item.findtext("atom:published", namespaces=ns) or "").strip()
        tags  = match_keywords(f"{title} {desc}")
        if tags:
            alerts.append(Alert(feed=feed_name, title=title, link=link, published=pub, tags=tags, snippet=desc[:200]))
    return alerts


def run(save_csv: bool = True) -> list:
    all_alerts = []
    print(f"[gods_eye_rss] Scanning {len(FEEDS)} feeds at {datetime.now(timezone.utc).isoformat()}\n")
    for feed_meta in FEEDS:
        root = fetch_feed(feed_meta["url"])
        if root is None:
            continue
        alerts = parse_feed(root, feed_meta["name"])
        all_alerts.extend(alerts)
        if alerts:
            print(f"  {feed_meta['name']}: {len(alerts)} alert(s) — {set(t for a in alerts for t in a.tags)}")
    print(f"\n[gods_eye_rss] Total alerts: {len(all_alerts)}")
    if save_csv and all_alerts:
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["feed","title","link","published","tags","snippet"])
            w.writeheader()
            for a in all_alerts:
                w.writerow({"feed": a.feed, "title": a.title, "link": a.link,
                            "published": a.published, "tags": "|".join(a.tags), "snippet": a.snippet})
        print(f"[gods_eye_rss] Saved → {OUTPUT_PATH}")
    return all_alerts


if __name__ == "__main__":
    run()
