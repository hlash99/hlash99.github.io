#!/usr/bin/env python3
"""Server-side data for the hub's Morning Brief panel. Runs in GitHub Actions; no Claude.

Writes live.json: date, weather (NWS), AAPL (stockanalysis.com), car listings (Cavallino
Drift + duPont Registry + Bring a Trailer) and news (NYT, NPR, BBC feeds). During the midterm
window the news turns to election stories and adds seat totals. A source that fails keeps
its previous value, so one bad fetch never blanks the panel.
"""
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "live.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
LAT_LON = "37.4636,-122.1983"  # Menlo Park
EXCLUDED_URLS = {"https://bringatrailer.com/listing/2014-ferrari-458-spider-40/"}  # about 7 years in Florida
MAX_458, MAX_NSX, MIN_MILES = 300000, 160000, 30000
FEEDS = (("NYT", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
         ("NPR", "https://feeds.npr.org/1001/rss.xml"),
         ("BBC", "https://feeds.bbci.co.uk/news/rss.xml?edition=us"))
# Pacific wall-clock window for the midterm results view; news runs before and after it.
MIDTERMS_START, MIDTERMS_END = "2026-11-03T15:00", "2026-11-08T00:00"
ELECTION_WORDS = re.compile(r"election|midterm|senate|house|governor|ballot|vote|congress|democrat|republican|race", re.I)
SKIP_WORDS = re.compile(r"crossword|wordle|quiz|in photos|connections|horoscope|^opinion", re.I)


def get(url, accept=None):
    headers = {"User-Agent": UA, "Accept-Language": "en-US,en"}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")


def pacific_now():
    now = datetime.now(timezone.utc)
    # US DST: second Sunday in March to first Sunday in November, 2 AM local.
    y = now.year
    mar = datetime(y, 3, 8, 10, tzinfo=timezone.utc)
    start = mar + timedelta(days=(6 - mar.weekday()) % 7)
    nov = datetime(y, 11, 1, 9, tzinfo=timezone.utc)
    end = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return now.astimezone(timezone(timedelta(hours=-7 if start <= now < end else -8)))


def weather():
    ua = "application/geo+json"
    point = json.loads(get(f"https://api.weather.gov/points/{LAT_LON}", ua))
    periods = json.loads(get(point["properties"]["forecast"], ua))["properties"]["periods"]
    day = next(p for p in periods if p["isDaytime"])
    night = next(p for p in periods if not p["isDaytime"])
    s = day["shortForecast"]
    low = s.lower()
    icon = ("storm" if "thunder" in low else "rain" if re.search("rain|shower|drizzle", low)
            else "fog" if "fog" in low else "wind" if "wind" in low
            else "partly" if "partly" in low or "mostly sunny" in low
            else "cloud" if "cloud" in low else "sun")
    label = "Tomorrow · " + s if periods[0] is not day else s
    return {"high": day["temperature"], "low": night["temperature"], "summary": label[:34], "icon": icon}


def stock():
    page = get("https://stockanalysis.com/stocks/aapl/")
    q = re.search(r"quote:\{(.*?)\}", page, re.S).group(1)

    def num(key):
        m = re.search(rf"(?:^|,){key}:(-?[\d.]+)", q)
        return float(m.group(1)) if m else None

    def txt(key):
        m = re.search(rf'(?:^|,){key}:"([^"]*)"', q)
        return m.group(1) if m else ""

    price, pct = num("p"), num("cp")
    out = {"symbol": "AAPL", "price": f"{price:.2f}", "change_pct": f"{pct:+.2f}%", "dir": "up" if pct >= 0 else "down"}
    if txt("ms") != "open":
        out["session"] = "Close"
    ep, ecp = num("ep"), num("ecp")
    if ep is not None and ecp is not None:
        pre = "pre" in txt("es").lower()
        out["after"] = {"label": "Pre-market" if pre else "After hours", "price": f"{ep:.2f}",
                        "change_pct": f"{ecp:+.2f}%", "dir": "up" if ecp >= 0 else "down"}
    return out


def money(n):
    return f"${n:,.0f}"


def cavallino_458():
    page = get("https://cavallinodrift.com/458spider/")
    dealer = {v: html.unescape(h) for h, v in
              re.findall(r'href="(https://[^"]+)"[^>]*aria-label="Dealer&#39;s listing for (\w+)', page)}
    dealer.update({v: html.unescape(h) for h, v in
                   re.findall(r'href="(https://[^"]+)"[^>]*aria-label="Dealer\'s listing for (\w+)', page)})
    start = page.index(">Active listings")
    body = page[start:page.index("<h2", start)]
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S):
        c = [html.unescape(re.sub(r"<[^>]+>", "", td)).strip() for td in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(c) < 8 or not c[7].startswith("ZFF"):
            continue
        miles, ask = int(re.sub(r"\D", "", c[2]) or 0), int(re.sub(r"\D", "", c[3]) or 0)
        if miles <= MIN_MILES or not 0 < ask <= MAX_458:
            continue
        seller, _, st = c[6].rpartition(",")
        rows.append({"vin": c[7], "car": f"{c[0]} 458 Spider", "color": c[1], "miles": f"{round(miles / 1000)}k",
                     "price": money(ask), "seller": seller.strip(), "location": st.strip(),
                     "href": dealer.get(c[7]) or f"https://cavallinodrift.com/vin/?v={c[7]}",
                     "source": "Cavallino Drift"})
    return rows


def dupont(path):
    page = get(f"https://www.dupontregistry.com/autos/results/{path}")
    for blob in re.findall(r"<script[^>]+ld\+json[^>]*>(.*?)</script>", page, re.S):
        if "itemListElement" in blob and "Car" in blob:
            return [e["item"] for e in json.loads(blob)["itemListElement"]]
    return []


def dupont_nsx():
    rows = []
    for i in dupont("acura/nsx"):
        vin = i.get("vehicleIdentificationNumber", "")
        year = int(i.get("vehicleModelDate") or 0)
        miles = (i.get("mileageFromOdometer") or {}).get("value") or 0
        offer = i.get("offers") or {}
        price = offer.get("price") or 0
        if not (1997 <= year <= 2001 and vin.startswith("JH4NA2") and miles > MIN_MILES and 0 < price <= MAX_NSX):
            continue
        text = get(i["url"])  # the results page doesn't say targa or gearbox; the listing does
        if not re.search(r"NSX-T|Targa", text) or not re.search(r"6[- ]Speed|Manual", text):
            continue
        addr = (offer.get("seller") or {}).get("address") or {}
        city = ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion")) if x)
        rows.append({"vin": vin, "car": f"{year} NSX-T 6-speed", "color": i.get("color") or "—",
                     "miles": f"{round(miles / 1000)}k", "price": money(price),
                     "seller": (offer.get("seller") or {}).get("name", ""), "location": city,
                     "href": i["url"], "source": "duPont Registry"})
    return rows


def bat_live():
    page = get("https://bringatrailer.com/auctions/")
    data = json.loads(re.search(r"var auctionsCurrentInitialData = (\{.*?\});\s*\n", page, re.S).group(1))
    spiders, nsx = [], []
    for x in data.get("items", []):
        title, url, bid = html.unescape(x.get("title", "")), x.get("url", ""), x.get("current_bid") or 0
        if url in EXCLUDED_URLS or not x.get("active"):
            continue
        blurb = html.unescape(re.sub(r"<[^>]+>", "", x.get("excerpt", "")))
        m = re.search(r"(\d+)k[- ]miles?", title + " " + blurb, re.I)
        miles = int(m.group(1)) * 1000 if m else None
        over = miles is None or miles > MIN_MILES
        end = datetime.fromtimestamp(x.get("timestamp_end") or 0, timezone.utc).strftime("%b %-d")
        row = {"car": title[:60], "color": "—", "miles": f"{miles // 1000}k" if miles else "—",
               "price": f"{money(bid)} bid, ends {end}", "seller": "BaT auction", "location": "",
               "href": url, "source": "BaT"}
        if "458 Spider" in title and over and bid <= MAX_458:
            spiders.append(row)
        elif re.search(r"(1997|1998|1999|2000|2001) Acura NSX-T 6-Speed", title) and over and bid <= MAX_NSX:
            nsx.append(row)
    return spiders, nsx


def headlines(only=None, limit=4):
    """Top stories in the feed's own order, deduped across feeds."""
    out, seen, errors = [], set(), 0
    for name, url in FEEDS:
        try:
            feed = get(url)
        except Exception as e:
            print(f"{name} feed failed: {e}", file=sys.stderr)
            errors += 1
            continue
        for item in re.findall(r"<item>(.*?)</item>", feed, re.S):
            t = re.search(r"<title[^>]*>(.*?)</title>", item, re.S)
            h = re.search(r"<link>(.*?)</link>", item, re.S)
            if not t or not h:
                continue
            clean = lambda x: html.unescape(re.sub(r"<!\[CDATA\[|\]\]>", "", x)).strip()
            title, href = clean(t.group(1)), clean(h.group(1))
            key = re.sub(r"\W+", "", title.lower())[:40]
            if not href.startswith("https://") or key in seen or SKIP_WORDS.search(title):
                continue
            if only and not only.search(title):
                continue
            seen.add(key)
            out.append({"title": title[:110], "href": href[:300], "source": name})
            if len(out) >= limit:
                return out
    if errors == len(FEEDS):
        raise RuntimeError("all news feeds failed")
    return out


def seat_value(text, field):
    """Infobox number for `field`: (low, high), e.g. (253, 253), or (52, 54) while races are
    uncalled. Comments, bold quotes, templates and refs are stripped first: a bolded
    '''52–54''' used to defeat the old digits-right-after-= regex and drop the Democrats."""
    m = re.search(rf"^\|\s*{field}\s*=([^\n]*)", text, re.M)
    if not m:
        return None
    v = re.sub(r"<!--.*?-->|<ref[^>]*/>|<ref.*?</ref>|'{2,}", "", m.group(1))
    while True:
        w = re.sub(r"\{\{[^{}]*\}\}", "", v)  # {{efn|...}}, innermost first
        if w == v:
            break
        v = w
    n = re.fullmatch(r"(\d+)(?:\s*[–—-]\s*(\d+))?", v.strip())
    if not n:
        return None
    lo, hi = int(n.group(1)), int(n.group(2) or n.group(1))
    return (lo, hi) if lo <= hi else None


def chamber(page, field, total):
    """Seats each side holds in the next Congress, from Wikipedia's infobox, counting only
    called races (the low end of a range). The Senate's seats_afterN is the whole chamber
    of 100 incl. holdovers (its seatsN would be races won this cycle and is left blank); the
    House has no seats_afterN, and with all 435 seats up its seatsN is the chamber total.
    None until editors fill them in, or whenever the numbers don't add up."""
    url = f"https://en.wikipedia.org/w/api.php?action=parse&page={page}&prop=wikitext&section=0&format=json&formatversion=2"
    text = json.loads(get(url))["parse"]["wikitext"]
    d = r = 0
    have_d = have_r = False
    for n in range(1, 8):
        party = re.search(rf"^\|\s*party{n}\s*=([^\n]*)", text, re.M)
        if not party or not party.group(1).strip():
            continue
        name, seats = party.group(1), seat_value(text, f"{field}{n}")
        if seats is None:
            if re.search(r"Democratic|Republican", name):
                return None  # a major party without a readable total: don't guess
            continue
        if "Republican" in name:
            r, have_r = r + seats[0], True
        elif "Democratic" in name:
            d, have_d = d + seats[0], True
        elif "Independent" in name and re.search(r"caucus(?:es)? with the Democrats", text):
            d += seats[0]  # independents caucus with Democrats
        elif seats[0]:
            return None  # a third party or non-caucusing independent holding seats
    uncalled = total - d - r
    if not (have_d and have_r) or uncalled < 0 or d + r < total // 2:
        return None
    return {"d": d, "r": r, "uncalled": uncalled, "total": total}


def election():
    out = {}
    for key, page, field, total in (("senate", "2026_United_States_Senate_elections", "seats_after", 100),
                                    ("house", "2026_United_States_House_of_Representatives_elections", "seats", 435)):
        try:
            out[key] = chamber(page, field, total)
        except Exception as e:
            print(f"{key} results failed: {e}", file=sys.stderr)
            out[key] = None
    return out


def papers():
    """Recent Khodadoust / Stanford Mavi papers as (id, title, link), newest first."""
    term = "Khodadoust+MS%5BAuthor%5D+OR+(Mavi%5BAuthor%5D+AND+Stanford%5BAffiliation%5D)"
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    ids = json.loads(get(f"{base}/esearch.fcgi?db=pubmed&term={term}&sort=pub_date&retmax=20&retmode=json&reldate=365&datetype=edat"))
    ids = ids["esearchresult"]["idlist"]
    if not ids:
        return []
    res = json.loads(get(f"{base}/esummary.fcgi?db=pubmed&id={','.join(ids)}&retmode=json"))["result"]
    return [(i, html.unescape(res[i].get("title", "")).rstrip("."), f"https://pubmed.ncbi.nlm.nih.gov/{i}/") for i in ids if i in res]


def raffle_alert():
    """Public counts from substack-brief's Yahoo job; the baseline is the ticket confirmation."""
    f = json.loads(get("https://raw.githubusercontent.com/hlash99/substack-brief/main/mail_flags.json"))
    if f.get("raffle_messages", 0) > 1 or f.get("latest", "") > "2026-10-21":
        return f"New raffle message in Yahoo ({f.get('latest', '')})"
    return None


KINDS = (("politics", r"election|midterm|senate|house|congress|governor|vote|ballot|trump|democrat|republican|poll|politic|campaign"),
         ("markets", r"stock|shares|s&p|dow|nasdaq|market|earnings|investor|bond|fed\b|rates?\b"),
         ("economy", r"jobs|inflation|economy|tariff|trade|prices|wages|gdp|budget|tax"),
         ("weather", r"storm|hurricane|flood|wildfire|heat wave|snow|earthquake|tornado"),
         ("health", r"health|vaccine|virus|hospital|cancer|disease|drug|measles|flu|stroke"),
         ("tech", r"\bai\b|apple|google|tech|software|chip|openai|meta|amazon|microsoft"),
         ("sports", r"game|season|nfl|nba|mlb|world series|cup|marathon|coach|playoff"),
         ("local", r"california|bay area|san francisco|oakland|san jose|palo alto|menlo park"))
NUMBER = re.compile(r"(\$?\d[\d,.]*\s?(?:%|percent|billion|million|trillion|bn|m\b|k\b)?)(\s+(?:dead|killed|injured|jobs|years|days|seats|points|people|homes))?", re.I)


def tile(title, href, source, kind=None, label=None, stat=None, direction=None):
    """One graphic tile: icon kind, small label, one big figure, short headline, source."""
    if not kind:
        kind = next((k for k, rx in KINDS if re.search(rf"\b(?:{rx})", title, re.I)), "world")
    if stat is None:
        m = NUMBER.search(title)
        if m and not re.fullmatch(r"(19|20)\d\d", m.group(1).strip()):
            stat = (m.group(1) + (m.group(2) or "")).strip()
    t = {"kind": kind, "label": (label or kind).upper()[:18], "title": title[:110], "href": href, "source": source}
    if stat:
        t["stat"] = stat[:14]
    if direction:
        t["dir"] = direction
    return t


def merge_alerts(prev, found, today):
    """Keep each alert for three days from when it first appeared. An alert found again is
    rewritten from the current data (an auction's bid moves) but keeps its first date."""
    out = {}
    for k, v in (prev.get("alerts") or {}).items():
        v = dict(v)
        if v.get("kind") == "car":  # repair entries written before the bid/blank-field fix
            if re.fullmatch(r"\$0( bid)?", v.get("stat", "")):
                v.pop("stat")
            v["title"] = re.sub(r"[,\s]+$", "", re.sub(r", [—-] mi\b", "", v.get("title", "")))
        out[k] = v
    for key, title, href, extra in found:
        date = out.get(key, {}).get("date", today)
        out[key] = dict(extra, title=title[:110], href=href, date=date)
    cutoff = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
    return {k: v for k, v in out.items() if v["date"] >= cutoff}


def attempt(name, fn, fallback, stale):
    try:
        return fn()
    except Exception as e:
        print(f"{name} failed: {e}", file=sys.stderr)
        stale.append(name)
        return fallback


def main():
    try:
        prev = json.load(open(OUT))
    except (OSError, ValueError):
        prev = {}
    seen = dict(prev.get("first_seen") or {})
    first_run = not seen
    history = json.load(open(os.path.join(ROOT, "data", "car_history.json")))
    stale = []
    now = pacific_now()
    today = now.strftime("%Y-%m-%d")

    stamp = now.strftime("%Y-%m-%dT%H:%M")
    phase = "midterms" if MIDTERMS_START <= stamp < MIDTERMS_END else "news"
    only = ELECTION_WORDS if phase == "midterms" else None
    news = attempt("news", lambda: headlines(only), prev.get("news"), stale)
    races = election() if phase == "midterms" else None
    w = attempt("weather", weather, prev.get("weather"), stale)
    s = attempt("stock", stock, prev.get("stock"), stale)
    prev_rows = {r.get("source"): [] for r in prev.get("cars", []) if r.get("source")}
    for r in prev.get("cars", []):
        if r.get("source"):
            prev_rows[r["source"]].append(r)
    keep = lambda *src: [r for x in src for r in prev_rows.get(x, [])]
    spider = attempt("cavallino", cavallino_458, keep("Cavallino Drift"), stale)
    nsx = attempt("dupont", dupont_nsx, keep("duPont Registry"), stale)
    bat = attempt("bat", bat_live, None, stale)
    bat_s, bat_n = bat if bat else ([], [])
    if bat is None:
        for r in keep("BaT"):
            (bat_n if "NSX" in r["car"] else bat_s).append(r)

    def finish(rows):
        out = []
        for r in rows:
            key = r.get("vin") or r["href"]
            verdict = history.get(r.get("vin", ""))
            if verdict == "excluded" or re.search(r"(^|[\s,])FL$", r.get("location", "")):
                continue  # Hassan doesn't want Florida-history or Florida-listed cars
            if key not in seen:
                seen[key] = "2000-01-01" if first_run else today
            flags = []
            if seen[key] >= (now - timedelta(days=2)).strftime("%Y-%m-%d"):
                flags.append("New")
            if verdict != "ok":
                flags.append("History unconfirmed")
            clean = r.get("vin") in history.get("_clean_title", [])
            r = dict(r, title="Clean (Carfax)" if clean else "Title not stated", flag=", ".join(flags))
            out.append(r)
        return out

    nsx_rows, spider_rows = finish(nsx + bat_n), finish(spider + bat_s)
    cars = []
    if nsx_rows:
        cars += [{"group": "Acura NSX-T"}] + nsx_rows
    if spider_rows:
        cars += [{"group": "Ferrari 458 Spider"}] + spider_rows

    # Things that matter to Hassan go first; headlines only fill what's left.
    found = []
    known = prev.get("papers_seen")
    listed = attempt("papers", papers, None, stale)
    seen_papers = set(known or [])
    # papers_new outlives the 3-day alerts: the hub pins each paper for 30 days from first seen.
    papers_new = {p["id"]: p for p in (prev.get("papers_new") or []) if p.get("id")}
    if listed is not None:
        fresh = [p for p in listed if p[0] not in seen_papers]
        if known is not None:
            found += [(f"paper:{i}", t, h, {"kind": "science", "label": "Khodadoust paper", "stat": "New", "source": "PubMed"})
                      for i, t, h in fresh]
            for i, t, h in fresh:
                papers_new.setdefault(i, {"id": i, "title": t[:300], "href": h, "date": today})
        seen_papers |= {p[0] for p in listed}
    month_ago = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    papers_new = sorted((p for p in papers_new.values() if p.get("date", "") >= month_ago),
                        key=lambda p: p["date"], reverse=True)
    note = attempt("raffle", raffle_alert, None, stale)
    if note:
        found.append(("raffle", note, "https://mail.yahoo.com/", {"kind": "you", "label": "LADD raffle", "stat": "Mail", "source": "Yahoo"}))
    was = {r.get("vin") or r.get("href"): r for r in prev.get("cars", []) if not r.get("group")}
    # Only the first $ amount: BaT rows read "$12,345 bid, ends Nov 12".
    digits = lambda r: int(re.sub(r"\D", "", str(r.get("price", "")).split(" ")[0]) or 0)

    def price_stat(r):
        if not re.search(r"\bbid\b", str(r.get("price", ""))):
            return r["price"].split(" ")[0]
        n = digits(r)
        return "No bids" if not n else f"${n / 1e6:.1f}M bid" if n >= 1e6 else f"${round(n / 1000)}k bid" if n >= 1000 else f"{money(n)} bid"

    prev_alerts = prev.get("alerts") or {}
    for r in nsx_rows + spider_rows:
        key = r.get("vin") or r["href"]
        old_row = was.get(key)
        label = r["car"].replace(" 6-speed", "")
        is_new = "New" in r["flag"]
        if is_new or f"car:{key}" in prev_alerts:  # re-found every run so the bid stays current
            miles = r.get("miles")
            bits = [label, f"{miles} mi" if miles and miles != "—" else "", r.get("location") or ""]  # skip blanks
            found.append((f"car:{key}", "New listing: " + ", ".join(b for b in bits if b), r["href"],
                          {"kind": "car", "label": "New listing", "stat": price_stat(r), "source": r.get("source", "")}))
        if not is_new and old_row and digits(r) and digits(r) < digits(old_row):
            found.append((f"drop:{key}:{digits(r)}", f"{label}, was {old_row['price']}", r["href"],
                          {"kind": "car", "label": "Price cut", "stat": price_stat(r), "dir": "down", "source": r.get("source", "")}))
    alerts = merge_alerts(prev, found, today)
    picks = [tile(v["title"], v["href"], v.get("source", ""), v.get("kind", "you"), v.get("label"), v.get("stat") or "New", v.get("dir"))
             for v in sorted(alerts.values(), key=lambda a: a["date"], reverse=True)]
    for p in picks:
        p["alert"] = True
    heads = [tile(n["title"], n["href"], n.get("source", "")) for n in (news or [])]
    extra = []
    if phase == "news" and stamp < MIDTERMS_START:
        days = (datetime.strptime(MIDTERMS_START[:10], "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days
        lead = next((h for h in heads if h["kind"] == "politics"), None)
        if lead:
            heads.remove(lead)
        extra.append(tile(lead["title"] if lead else "Polls close Tuesday night", lead["href"] if lead else "https://www.nytimes.com/",
                          lead["source"] if lead else "NYT", "politics", "Midterms", "Today" if days <= 0 else f"{days} day{'s' if days != 1 else ''}"))
    if phase == "midterms":
        for name, v in (("Senate", (races or {}).get("senate")), ("House", (races or {}).get("house"))):
            if v:
                left = v["uncalled"]
                # Short on purpose: the hub tile clamps captions to two lines.
                tail = "all called" if not left else f"{left} uncalled"
                extra.append(tile(f"Seats of {v['total']} · {tail}", "https://en.wikipedia.org/wiki/2026_United_States_elections",
                                  "Wikipedia", "politics", name, f"D {v['d']} · R {v['r']}"))
    items = (picks + extra + heads)[:5]  # AAPL already sits in the strip above

    out = {"updated": now.isoformat(timespec="minutes"), "date": today,
           "date_line": now.strftime("%A · %B %-d %Y"), "weather": w, "stock": s, "cars": cars,
           "phase": phase, "items": items, "news": news, "alerts": alerts,
           "papers_seen": sorted(seen_papers), "papers_new": papers_new, "election": races, "stale": stale, "first_seen": seen}
    if len(stale) == 8:
        print("every source failed; leaving live.json alone", file=sys.stderr)
        return
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
