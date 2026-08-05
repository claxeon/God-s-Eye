#!/usr/bin/env python3
"""
God's Eye — Pacific Carrier Watch

Tracks US aircraft carrier disposition by theater, against the tripwire the
China actor node already specifies for itself:

    "full invasion: only if US carrier presence < 2 battle groups in Pacific"
        -- Actors/Nodes/China.md, behaviour model

That condition has sat in the node untracked. This script counts it.

WHY IT MATTERS
--------------
The China node ranks "Taiwan contingency prep -- deplete US military/political
bandwidth" at priority 3 of its utility function. The Gulf war is the
depletion mechanism: every carrier pulled into 5th Fleet is one not in the
Western Pacific. This measures whether the distraction the node theorises is
actually happening, rather than assuming it.

SOURCE
------
USNI News "Fleet and Marine Tracker" RSS -- the canonical open-source fleet
disposition report, published roughly weekly with full content in the feed.
Posts are structured with <h#> regional headers ("In the Philippine Sea",
"In the Arabian Sea", ...), so carriers are attributed to a theater by the
section they appear under rather than by fragile proximity matching.

HONEST LIMITS
-------------
- USNI publishes ~weekly and can lag; the report date is always printed. Treat
  a stale report as stale, not as current truth.
- "Carrier present in theater" is not the same as "carrier combat-ready in
  theater" -- in-port, working-up and exercise participation all count as
  present here. The node's tripwire says "battle groups", which is a stricter
  concept than hull count. This is a proxy, and deliberately a generous one:
  it will over-count Pacific presence, so a LOW reading is meaningful while a
  high reading is weak evidence.
- Headers are prose written by humans and occasionally change wording. Any
  header that does not map to a known theater is reported as UNCLASSIFIED
  rather than silently dropped.

Usage:
    python3 carrier_watch.py            # human-readable
    python3 carrier_watch.py --json     # machine-readable
"""

import argparse
import html
import json
import re
import subprocess
import sys
import warnings
from datetime import date

warnings.filterwarnings("ignore")

# FEED SELECTION -- this matters, and cost the analysis a retraction once.
#
# 2026-07-29: the CATEGORY feed (/category/fleet-tracker/feed) had stalled at
# July 7 while July 13, 20 and 27 were already published. Trusting it made this
# script report a 22-day-old disposition as current, across the Jazan/Yanbu
# strikes and the ceasefire collapse, and a "tripwire MET" conclusion was drawn
# and then had to be withdrawn.
#
# The SITE-WIDE feed (/feed) was current and carried full article bodies. Post
# pages themselves return a ~6KB empty shell to curl regardless of User-Agent,
# Accept or Referer, so RSS is the only readable path -- but it has to be the
# right RSS. Read site-wide first, fall back to the category feed, and always
# cross-check both against the category PAGE, which lists what has actually
# been published even when a feed is behind.
FEED = "https://news.usni.org/feed"
CATEGORY_FEED = "https://news.usni.org/category/fleet-tracker/feed"
INDEX_URL = "https://news.usni.org/category/fleet-tracker"
TRIPWIRE_PACIFIC_CARRIERS = 2   # China node: invasion window opens below this
MAX_REPORT_AGE_DAYS = 14        # beyond this the tripwire is NOT asserted

# Theater classification. Order matters -- first match wins, so put the
# specific bodies of water before the broad ones.
THEATERS = [
    # Forward-deployed western Pacific -- the count that actually bears on a
    # Taiwan contingency. Listed first so it wins over the broader PACIFIC_REAR.
    ("PACIFIC_FWD", [
        "philippine sea", "south china sea", "east china sea", "sea of japan",
        "western pacific", "guam", "yokosuka", "okinawa", "korea", "taiwan",
        "japan", "sasebo", "yellow sea",
        # SE Asian ports and waters. Added 2026-08-03 after CVN-73 showed up
        # under "In Da Nang, Vietnam" and fell through to UNCLASSIFIED, making
        # the forward count read 0 when it was 1. Port calls still count as
        # present-in-theatre under this script's deliberately generous rule --
        # though note a hull alongside in a foreign port is less available than
        # one underway, which the hull-count measure cannot distinguish.
        "vietnam", "da nang", "danang", "cam ranh", "philippines", "subic",
        "manila", "singapore", "malaysia", "indonesia", "thailand", "brunei",
        "south east asia", "southeast asia", "strait of malacca", "palau",
    ]),
    # Eastern Pacific / home ports / mid-Pacific exercises. Present in the
    # ocean, but days-to-weeks from a Taiwan contingency.
    ("PACIFIC_REAR", [
        "eastern pacific", "pearl harbor", "hawaii", "san diego", "bremerton",
        "everett", "pacific",
    ]),
    ("CENTCOM", [
        "arabian sea", "arabian gulf", "persian gulf", "red sea", "gulf of oman",
        "gulf of aden", "north arabian", "middle east", "bab el-mandeb",
        "indian ocean",
    ]),
    ("EUROPE", [
        "mediterranean", "adriatic", "aegean", "baltic", "north sea",
        "norwegian sea", "belgium", "italy", "greece", "spain", "france",
        "united kingdom", "england", "scotland",
    ]),
    ("ATLANTIC/HOME", [
        "atlantic", "norfolk", "new york", "florida", "caribbean",
        "venezuela", "south america", "brazil",
    ]),
]

CARRIER_RE = re.compile(r"USS\s+([A-Z][A-Za-z\.\s'-]{2,30}?)\s*\((CVN[-\s]?\d+)\)")


def classify(header):
    h = header.lower()
    for theater, keys in THEATERS:
        for k in keys:
            if k in h:
                return theater
    return "UNCLASSIFIED"


def _curl(url):
    r = subprocess.run(
        ["curl", "-sL", "--max-time", "45", "-A", "Mozilla/5.0", url],
        capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def newest_published_date():
    """Date of the newest Fleet Tracker that EXISTS, from the category page.

    The category page renders fine to curl. Individual post pages do NOT --
    they return a ~6KB shell with zero article content regardless of
    User-Agent, Accept or Referer headers (tested 2026-07-29). So the page
    tells us what has been published; only the RSS gives us readable bodies.
    Knowing the gap between those two is the point of this function."""
    page = _curl(INDEX_URL)
    dates = set()
    for _, y, m, d in re.findall(
            r'href="(https://news\.usni\.org/(\d{4})/(\d{2})/(\d{2})/'
            r'[^"]*fleet-and-marine-tracker[^"]*)"', page):
        try:
            dates.add(date(int(y), int(m), int(d)))
        except ValueError:
            continue
    return max(dates) if dates else None


def _trackers_from(feed_url, label):
    """All Fleet Tracker entries in a feed, newest first, with bodies."""
    import feedparser
    out = []
    for e in feedparser.parse(feed_url).entries:
        if "fleet and marine tracker" not in e.get("title", "").lower():
            continue
        body = (e["content"][0].get("value", "") if e.get("content")
                else e.get("summary", ""))
        mm = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", e.get("link", ""))
        if not mm:
            continue
        out.append({"title": e.get("title", ""),
                    "published": date(*map(int, mm.groups())).isoformat(),
                    "link": e.get("link", ""), "body": body, "source": label})
    out.sort(key=lambda r: r["published"], reverse=True)
    return out


def latest_tracker():
    """Newest readable Fleet Tracker across both feeds.

    Both are checked because either can stall independently; whichever has the
    newest post with a usable body wins."""
    cands = (_trackers_from(FEED, "rss-sitewide")
             + _trackers_from(CATEGORY_FEED, "rss-category"))
    cands = [c for c in cands if c["body"] and "CVN" in c["body"]]
    if not cands:
        return None
    cands.sort(key=lambda r: r["published"], reverse=True)
    return cands[0]


def parse_sections(body):
    """Split the post body into (header, text) by <h#> tags."""
    parts = re.split(r"<h[1-6][^>]*>(.*?)</h[1-6]>", body, flags=re.S)
    # parts = [pre, header1, text1, header2, text2, ...]
    out = []
    for i in range(1, len(parts) - 1, 2):
        hdr = re.sub(r"<[^>]+>", "", html.unescape(parts[i])).strip()
        txt = re.sub(r"<[^>]+>", " ", html.unescape(parts[i + 1]))
        txt = re.sub(r"\s+", " ", txt)
        if hdr:
            out.append((hdr, txt))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    post = latest_tracker()
    if not post:
        print("ERROR: no Fleet and Marine Tracker post found in feed",
              file=sys.stderr)
        return 2

    sections = parse_sections(post["body"])
    by_theater = {}
    seen = {}          # hull -> (name, theater, header)
    last_geo = None    # USNI nests "Carrier Strike Group N" subheaders under a
                       # geographic header; those inherit the parent theater
                       # rather than falling out as UNCLASSIFIED.
    for hdr, txt in sections:
        theater = classify(hdr)
        if theater == "UNCLASSIFIED" and last_geo:
            theater = last_geo
        elif theater != "UNCLASSIFIED":
            last_geo = theater
        for m in CARRIER_RE.finditer(txt):
            name = " ".join(m.group(1).split())
            hull = m.group(2).replace(" ", "-").upper()
            if not hull.startswith("CVN-"):
                hull = "CVN-" + hull.replace("CVN", "").lstrip("-")
            if hull in seen:
                continue      # first (most specific) section wins
            seen[hull] = (name, theater, hdr)
            by_theater.setdefault(theater, []).append(
                {"hull": hull, "name": name, "section": hdr})

    n_fwd = len(by_theater.get("PACIFIC_FWD", []))
    n_rear = len(by_theater.get("PACIFIC_REAR", []))
    n_pac = n_fwd + n_rear
    # The node's tripwire is about usable presence, so it is measured on the
    # FORWARD count. The all-Pacific total is reported alongside as context.
    tripped = n_fwd < TRIPWIRE_PACIFIC_CARRIERS

    # Staleness. A tripwire is a claim about NOW; past a freshness bound this
    # script reports the reading but explicitly refuses to assert the tripwire.
    age_days = None
    try:
        age_days = (date.today() - date.fromisoformat(post["published"])).days
    except (ValueError, TypeError):
        pass
    stale = age_days is None or age_days > MAX_REPORT_AGE_DAYS

    # Is a newer report published that we simply cannot read?
    newest = newest_published_date()
    behind_days = None
    if newest and post["published"]:
        try:
            behind_days = (newest - date.fromisoformat(post["published"])).days
        except ValueError:
            pass

    # Guard: zero carriers located means the parse failed, not that the Navy
    # has no carriers. Never assert a tripwire off an empty parse.
    parse_failed = len(seen) == 0

    if stale or parse_failed:
        tripped = None          # unknown, not False

    result = {
        "report_title": post["title"],
        "report_published": post["published"],
        "report_age_days": age_days,
        "report_link": post["link"],
        "pacific_carriers_forward": n_fwd,
        "pacific_carriers_rear": n_rear,
        "pacific_carriers_total": n_pac,
        "tripwire_threshold": TRIPWIRE_PACIFIC_CARRIERS,
        "tripwire_tripped": tripped,          # None = too stale to assert
        "tripwire_measured_on": "PACIFIC_FWD",
        "report_stale": stale,
        "max_report_age_days": MAX_REPORT_AGE_DAYS,
        "discovery_source": post.get("source"),
        "parse_failed": parse_failed,
        "newest_published_date": newest.isoformat() if newest else None,
        "unreadable_reports_behind_days": behind_days,
        "by_theater": {k: v for k, v in sorted(by_theater.items())},
        "total_carriers_located": len(seen),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("=" * 68)
    print("  Pacific Carrier Watch — China node invasion tripwire")
    print("=" * 68)
    print(f"  Source : {post['title']}")
    print(f"  Dated  : {post['published']}"
          + (f"   ({age_days}d old)" if age_days is not None else ""))
    print(f"  Via    : {post.get('source')}")
    if newest:
        print(f"  Newest published: {newest}"
              + (f"  ({behind_days}d newer than what we can read)"
                 if behind_days else ""))
    if behind_days:
        print("  ⚠️  A NEWER REPORT EXISTS BUT ITS CONTENT IS UNREACHABLE.")
        print("      USNI post pages return an empty shell to curl; only RSS")
        print("      carries bodies, and RSS lags. Disposition below is the")
        print("      newest READABLE one, not the newest published one.")
    if stale:
        print(f"  ⚠️  STALE (> {MAX_REPORT_AGE_DAYS}d) — tripwire NOT asserted")
    if parse_failed:
        print("  ⚠️  PARSE FAILED — zero carriers located; tripwire NOT asserted")
    print()
    for theater in sorted(by_theater):
        print(f"  {theater}  ({len(by_theater[theater])})")
        for c in by_theater[theater]:
            print(f"     {c['hull']:8} {c['name']:<26} [{c['section']}]")
    print()
    n_unc = len(by_theater.get("UNCLASSIFIED", []))
    print("  " + "-" * 64)
    if n_unc:
        # Loud, because an unclassified hull silently disappears from every
        # theatre count -- which is how the forward count read 0 on 2026-08-03.
        print(f"  ⚠️  {n_unc} carrier(s) UNCLASSIFIED — theatre keywords may need")
        print("      extending; counts below EXCLUDE them and may understate.")
    print(f"  PACIFIC FORWARD (WESTPAC): {n_fwd}   "
          f"tripwire: < {TRIPWIRE_PACIFIC_CARRIERS}")
    print(f"  Pacific rear (E.Pac/Hawaii/home): {n_rear}"
          f"    all-Pacific total: {n_pac}")
    if tripped is None:
        print("  ⚪ TRIPWIRE NOT ASSERTED — report too old to describe today")
    elif tripped:
        print("  🔴 TRIPWIRE CONDITION MET — China node's stated invasion")
        print("     window criterion is satisfied on this report.")
    else:
        print("  🟢 above tripwire")
    print()
    print("  NOTE: hull count in theater, not combat-ready battle groups.")
    print("        Deliberately generous — a LOW reading is the meaningful one.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
