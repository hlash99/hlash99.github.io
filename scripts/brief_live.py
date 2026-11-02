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


def headlines(only=None, limit=3):
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


def chamber(page):
    """Declared seat totals from Wikipedia's infobox. None until editors fill them in."""
    url = f"https://en.wikipedia.org/w/api.php?action=parse&page={page}&prop=wikitext&section=0&format=json&formatversion=2"
    text = json.loads(get(url))["parse"]["wikitext"]
    d = r = 0
    for n in range(1, 6):
        party = re.search(rf"\|\s*party{n}\s*=\s*([^\n|]*)", text)
        seats = re.search(rf"\|\s*seats_after{n}\s*=\s*(\d+)", text)
        if not party or not seats:
            continue
        if "Republican" in party.group(1):
            r += int(seats.group(1))
        else:
            d += int(seats.group(1))  # independents caucus with Democrats
    return {"d": d, "r": r} if d and r else None


def election():
    out = {}
    for key, page in (("senate", "2026_United_States_Senate_elections"),
                      ("house", "2026_United_States_House_of_Representatives_elections")):
        try:
            out[key] = chamber(page)
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


def merge_alerts(prev, found, today):
    """Keep each alert for three days from when it first appeared."""
    out = dict(prev.get("alerts") or {})
    for key, title, href in found:
        out.setdefault(key, {"title": title[:110], "href": href, "date": today})
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
            if verdict == "excluded":
                continue
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
    if listed is not None:
        fresh = [p for p in listed if p[0] not in seen_papers]
        if known is not None:
            found += [(f"paper:{i}", f"New Khodadoust paper: {t}", h) for i, t, h in fresh]
        seen_papers |= {p[0] for p in listed}
    note = attempt("raffle", raffle_alert, None, stale)
    if note:
        found.append(("raffle", note, "https://mail.yahoo.com/"))
    was = {r.get("vin") or r.get("href"): r for r in prev.get("cars", []) if not r.get("group")}
    digits = lambda r: int(re.sub(r"\D", "", str(r.get("price", "")).split(" ")[0]) or 0)
    for r in nsx_rows + spider_rows:
        key = r.get("vin") or r["href"]
        old_row = was.get(key)
        label = r["car"].replace(" 6-speed", "")
        if "New" in r["flag"]:
            found.append((f"car:{key}", f"New listing: {label}, {r['miles']} mi, {r['price']}", r["href"]))
        elif old_row and digits(r) and digits(r) < digits(old_row):
            found.append((f"drop:{key}:{digits(r)}", f"Price cut: {label}, {old_row['price']} to {r['price']}", r["href"]))
    alerts = merge_alerts(prev, found, today)
    picks = [dict(v, kind="alert") for v in sorted(alerts.values(), key=lambda a: a["date"], reverse=True)]
    items = (picks + [dict(n, kind="news") for n in (news or [])])[:3]

    out = {"updated": now.isoformat(timespec="minutes"), "date": today,
           "date_line": now.strftime("%A · %B %-d %Y"), "weather": w, "stock": s, "cars": cars,
           "phase": phase, "items": items, "news": news, "alerts": alerts,
           "papers_seen": sorted(seen_papers), "election": races, "stale": stale, "first_seen": seen}
    if len(stale) == 8:
        print("every source failed; leaving live.json alone", file=sys.stderr)
        return
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
