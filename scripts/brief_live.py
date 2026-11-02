#!/usr/bin/env python3
"""Server-side data for the hub's Morning Brief panel. Runs in GitHub Actions; no Claude.

Writes live.json: date, weather (NWS), AAPL (stockanalysis.com), and car listings
(Cavallino Drift + duPont Registry + Bring a Trailer). A source that fails keeps its
previous value, so one bad fetch never blanks the panel.
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
                     "href": f"https://cavallinodrift.com/vin/?v={c[7]}", "source": "Cavallino Drift"})
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

    out = {"updated": now.isoformat(timespec="minutes"), "date": today,
           "date_line": now.strftime("%A · %B %-d %Y"), "weather": w, "stock": s, "cars": cars,
           "stale": stale, "first_seen": seen}
    if len(stale) == 5:
        print("every source failed; leaving live.json alone", file=sys.stderr)
        return
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
