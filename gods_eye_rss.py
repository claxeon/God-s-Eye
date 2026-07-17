#!/usr/bin/env python3
"""
God's Eye — RSS Intelligence Brief Generator
Pulls from free RSS feeds, filters by God's Eye framework key terms,
classifies hits by leg and confidence tier, and writes a dated
intelligence brief into the Obsidian vault.

Vault path: /Users/leehutton/Downloads/God's Eye
Output:     /Users/leehutton/Downloads/God's Eye/Intelligence Briefs/Intelligence Brief - YYYY-MM-DD.md
"""

import feedparser
import datetime
import os
from collections import defaultdict

# ── RSS Feed Sources ──────────────────────────────────────────────────────
FEEDS = [
    ("Reuters Business",   "https://feeds.reuters.com/reuters/businessNews"),
    ("Reuters World",      "https://feeds.reuters.com/Reuters/worldNews"),
    ("EIA News",           "https://www.eia.gov/rss/news.xml"),
    ("OilPrice.com",       "https://oilprice.com/rss/main"),
    ("AP Business",        "https://rsshub.app/apnews/topics/business"),
    ("CNBC Energy",        "https://www.cnbc.com/id/19836768/device/rss/rss.html"),
    ("Yahoo Finance",      "https://finance.yahoo.com/news/rssindex"),
    ("Axios World",        "https://api.axios.com/feed/"),
    ("FT Energy",          "https://www.ft.com/energy?format=rss"),
    ("Hellenic Shipping",  "https://www.hellenicshippingnews.com/feed/"),
    ("TradeWinds",         "https://www.tradewindsnews.com/rss"),
    ("Natural Gas Intel",  "https://www.naturalgasintel.com/feed/"),
    ("Reuters Commodities","https://feeds.reuters.com/reuters/companyNews"),
    ("Nikkei Asia",        "https://asia.nikkei.com/rss/feed/nar"),
    ("ZeroHedge",          "https://feeds.feedburner.com/zerohedge/feed"),
]

# ── Key Terms Per Leg ─────────────────────────────────────────────────────
LEG_TERMS = {
    "Leg 1 — War / Energy Chokepoints": [
        "hormuz", "strait of hormuz", "iran", "irgc", "israel", "ceasefire",
        "brent", "wti", "south pars", "ras laffan", "lng", "force majeure",
        "houthi", "yanbu", "chokepoint", "crude oil", "oil price", "energy shock",
        "persian gulf", "mb/d", "barrels per day", "opec", "oil supply",
        "energy disruption", "gulf war", "oil embargo",
    ],
    "Leg 2 — GCC / Petrodollar": [
        "petrodollar", "yuan oil", "renminbi", "tic data", "treasury international capital",
        "dollar reserve", "gcc", "saudi peg", "dollar peg", "swift", "reserve currency",
        "dedollarization", "de-dollarization", "brics", "usd hegemony",
        "foreign exchange reserves", "sovereign wealth", "adia", "pif", "qia",
    ],
    "Leg 3 — Private Credit / NBFI": [
        "private credit", "private debt", "credit fund", "gating", "redemption freeze",
        "nav discount", "apollo", "barings", "blue owl", "nbfi", "private equity stress",
        "leveraged loan", "credit stress", "liquidity mismatch", "fund gates",
        "middle market lending", "direct lending",
    ],
    "Leg 4 — Rails / XRP / Stablecoin": [
        "xrp", "ripple", "cbdc", "stablecoin", "genius act", "clarity act",
        "digital dollar", "tether", "usdt", "usdc", "crypto regulation",
        "payment rail", "cross-border payment", "rlusd", "digital asset regulation",
    ],
    "Leg 5 — Food / Fertilizer": [
        "fertilizer", "urea", "qafco", "food security", "famine", "grain",
        "wheat", "food crisis", "agricultural", "nitrogen fertilizer",
        "ammonia", "food supply", "hunger", "wfp", "world food programme",
    ],
    "Leg 6 — Munitions / MIC": [
        "munitions", "ammunition", "defense production", "stockpile", "raytheon",
        "lockheed", "weapons supply", "military industrial", "artillery",
        "missile production", "defense spending", "arms production",
    ],
    "Leg 7 — Semiconductor / Taiwan": [
        "taiwan strait", "tsmc", "semiconductor", "advanced chip", "taiwan tension",
        "taiwan military", "pla navy", "chip war", "taiwan invasion",
        "taiwan blockade", "microchip", "taiwan crisis",
    ],
    "Leg 8 — Maritime / Insurance": [
        "shipping", "maritime", "lloyd", "war risk insurance", "tanker",
        "bab al-mandab", "red sea", "ais data", "freight rate",
        "shipping lane", "seafarer", "cargo route", "vessel attack",
        "shipping disruption", "container", "suez canal",
    ],
    "Leg 9 — AI / Labor": [
        "layoffs", "job cuts", "sahm rule", "recession indicator",
        "ai data center", "hyperscaler capex", "data center power",
        "unemployment rate", "labor market", "tech layoffs",
    ],
    "Cross-Cutting — JPY Carry": [
        "boj", "bank of japan", "yen", "jpy", "carry trade", "jgb",
        "ueda", "japanese yen", "dollar yen", "boj hike", "japan rate",
        "japanese bond", "yen weakness", "yen intervention", "katayama",
        "japan monetary policy",
    ],
}

# ── Critical Signal Watchlist (triggers an alert callout) ─────────────────
# Kept as plain substrings on purpose (matches the rest of this script's
# style), but each entry should be checked against real recent headlines
# when added, not just guessed — "bab al-mandab" alone missed the 2026-07-16
# sweep because feeds spelled it "Bab el-Mandeb"; found only by checking why
# a visibly major escalation produced zero critical hits.
CRITICAL_SIGNALS = [
    "bab al-mandab",
    "bab al-mandeb",
    "bab el-mandeb",
    "bab el-mandab",
    "hormuz reopens",
    "hormuz closed",
    "naval blockade",
    "iran blockade",
    "strikes exchanged",
    "saudi peg",
    "dollar peg review",
    "boj hike",
    "tic data",
    "force majeure",
    "fund gate",
    "tether",
    "yanbu",
    "south pars",
    "phase 5 famine",
    "carry trade unwind",
    "emergency cut",
]

# ── Source Confidence Classification ─────────────────────────────────────
HIGH_CONFIDENCE = ["reuters", "eia.gov", "ft.com", "nikkei", "ap ", "associated press",
                   "hellenic shipping", "tradewinds", "natural gas intel"]
SPECULATIVE     = ["zerohedge", "zero hedge"]

def classify_confidence(source_name):
    s = source_name.lower()
    for h in HIGH_CONFIDENCE:
        if h in s:
            return "🟢 CONFIRMED-SOURCE"
    for sp in SPECULATIVE:
        if sp in s:
            return "🔴 SPECULATIVE-SOURCE"
    return "🟡 UNVERIFIED"

def match_legs(title, summary):
    text = (title + " " + summary).lower()
    matched = []
    for leg, terms in LEG_TERMS.items():
        score = sum(1 for t in terms if t in text)
        if score > 0:
            matched.append((leg, score))
    matched.sort(key=lambda x: -x[1])
    return matched[:3]

def is_critical(title, summary):
    text = (title + " " + summary).lower()
    return any(sig in text for sig in CRITICAL_SIGNALS)

def fetch_feeds():
    items = []
    for name, url in FEEDS:
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
            for entry in feed.entries[:25]:
                title   = entry.get("title", "").strip()
                summary = entry.get("summary", entry.get("description", "")).strip()
                # Strip HTML tags roughly
                summary = " ".join(summary.split())
                import re
                summary = re.sub(r"<[^>]+>", "", summary)[:350]
                link    = entry.get("link", "")
                pub     = entry.get("published", "")
                items.append({
                    "source":    name,
                    "title":     title,
                    "summary":   summary,
                    "link":      link,
                    "published": pub,
                })
        except Exception as e:
            print(f"  ⚠ {name}: {e}")
    return items

def filter_and_classify(items):
    all_terms = [t for terms in LEG_TERMS.values() for t in terms]
    relevant = []
    seen_titles = set()
    for item in items:
        text = (item["title"] + " " + item["summary"]).lower()
        if any(t in text for t in all_terms):
            # Deduplicate by title
            key = item["title"].lower()[:60]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            item["legs"]       = match_legs(item["title"], item["summary"])
            item["confidence"] = classify_confidence(item["source"])
            item["critical"]   = is_critical(item["title"], item["summary"])
            relevant.append(item)
    return relevant

def write_brief(items, vault_path):
    today    = datetime.date.today().strftime("%Y-%m-%d")
    now      = datetime.datetime.now().strftime("%H:%M")
    out_dir  = os.path.join(vault_path, "Intelligence Briefs")
    os.makedirs(out_dir, exist_ok=True)
    filepath = os.path.join(out_dir, f"Intelligence Brief - {today}.md")

    by_leg = defaultdict(list)
    critical_items = []
    for item in items:
        if item["critical"]:
            critical_items.append(item)
        primary = item["legs"][0][0] if item["legs"] else "Unclassified"
        by_leg[primary].append(item)

    leg_order = list(LEG_TERMS.keys()) + ["Unclassified"]
    confirmed  = sum(1 for i in items if i["confidence"] == "🟢 CONFIRMED-SOURCE")
    unverified = sum(1 for i in items if i["confidence"] == "🟡 UNVERIFIED")
    speculative= sum(1 for i in items if i["confidence"] == "🔴 SPECULATIVE-SOURCE")

    lines = [
        "---",
        f"tags: [gods-eye, intelligence-brief, {today}]",
        f"date: {today}",
        f"generated: {today} {now}",
        f"total-items: {len(items)}",
        f"critical-signals: {len(critical_items)}",
        "---",
        "",
        f"# Intelligence Brief — {today}",
        "",
        f"> [!info] Auto-generated at {now}",
        f"> RSS sweep across {len(FEEDS)} sources. {len(items)} items matched God's Eye framework terms.",
        f"> 🟢 Confirmed-source: {confirmed} | 🟡 Unverified: {unverified} | 🔴 Speculative: {speculative}",
        f"> Cross-reference all items against [[Framework/Intelligence Confidence Tiers]] before acting.",
        "",
    ]

    # Critical signals section
    if critical_items:
        lines += [
            "> [!danger] Critical Signal Hits",
            f"> {len(critical_items)} item(s) matched the critical watchlist. Review immediately.",
            "",
            "## 🚨 Critical Signal Watchlist Hits",
            "",
        ]
        for item in critical_items:
            leg_tags = " · ".join([l[0].split("—")[0].strip() for l in item["legs"]])
            lines += [
                f"### {item['confidence']} {item['title']}",
                f"**Source:** {item['source']} | **Legs:** {leg_tags}",
                f"**Published:** {item.get('published', 'N/A')}",
                "",
                item["summary"],
                "",
                f"→ [Read]({item['link']})",
                "",
                "---",
                "",
            ]

    lines += ["---", "", "## All Items by Leg", ""]

    for leg in leg_order:
        if leg not in by_leg:
            continue
        leg_items = by_leg[leg]
        lines += [f"## {leg}", ""]
        for item in leg_items:
            leg_tags = " · ".join([l[0].split("—")[0].strip() for l in item["legs"]])
            critical_tag = " 🚨" if item["critical"] else ""
            lines += [
                f"### {item['confidence']}{critical_tag} {item['title']}",
                f"**Source:** {item['source']} | **Legs:** {leg_tags}",
                f"**Published:** {item.get('published', 'N/A')}",
                "",
                item["summary"],
                "",
                f"→ [Read]({item['link']})",
                "",
                "---",
                "",
            ]

    lines += [
        "",
        "*Back to [[God's Eye - Index]] | Previous briefs: [[Intelligence Briefs]]*",
    ]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✓ Brief written: {filepath}")
    print(f"  {len(items)} items | {len(critical_items)} critical signals")
    return filepath


if __name__ == "__main__":
    VAULT = "/Users/leehutton/Downloads/God's Eye"
    print("God's Eye RSS Intelligence Sweep")
    print(f"Feeds: {len(FEEDS)} | Legs: {len(LEG_TERMS)}")
    print()
    print("Fetching feeds...")
    raw   = fetch_feeds()
    print(f"Raw items: {len(raw)}")
    items = filter_and_classify(raw)
    print(f"Relevant items: {len(items)}")
    print()
    write_brief(items, VAULT)
