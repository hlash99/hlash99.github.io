#!/usr/bin/env python3
"""Server-side data for the hub's Morning Brief panel. Runs in GitHub Actions; no Claude.

Writes live.json: date, weather (NWS), AAPL (stockanalysis.com), car listings (Cavallino
Drift + duPont Registry + Bring a Trailer: 458 Spider, NSX-T, C8 Corvette Z06; the Z06 also
from CorvetteForum's C8 classifieds) and news (NYT, NPR, BBC feeds). A source that fails keeps
its previous value, so one bad fetch never blanks the panel.
"""
import html
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "live.json")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
LAT_LON = "37.4636,-122.1983"  # Menlo Park
EXCLUDED_URLS = {"https://bringatrailer.com/listing/2014-ferrari-458-spider-40/"}  # about 7 years in Florida
MAX_458, MAX_NSX, MIN_MILES = 300000, 160000, 30000  # no price cap on the Z06 watch
# C8 Z06 without the big wing: the Z07 / Ultimate Performance package and the carbon aero
# packages all bring the high wing. Excluded only on positive evidence; "no Z07" etc. don't count.
WING = re.compile(r"(?<!\d)Z07(?!\d)|Z06/07|high[- ]?wing|ultimate performance|carbon[\w -]{0,30}?\baero\b"
                  r"|carbon(?:[- ]fiber)?(?: rear)? (?:wing|spoiler)", re.I)
NOT_WING = re.compile(r"\b(?:no|non|without|deleted?)[- ](?:the |a )?(?:Z07|high[- ]?wing|carbon aero|aero|wing)\b", re.I)
CF_FORUM = "https://www.corvetteforum.com/forums/c8-corvettes-for-sale-178/"
CF_PAGES, CF_THREADS, CF_PAUSE = 3, 30, 1.0  # stay polite: index pages, thread fetches, seconds between
# Want-to-buy / trade / sold / closed threads, and dealer "New 2027" allocations.
CF_SKIP = re.compile(r"\b(?:WTB|WTT|ISO|wanted|looking for|ready to buy|sold|closed|no longer|off the market|"
                     r"traded|pending|deposit|delete[d]?)\b|\bnew 20\d\d\b", re.I)
FLORIDA = re.compile(r"\bFlorida\b|\bFL\b|\b(?:Miami|Tampa|Orlando|Jacksonville|Sarasota|Naples|Fort Lauderdale|"
                     r"Ft\.? Lauderdale|Boca Raton|West Palm|Palm Beach|Lakeland|Tallahassee|Fort Myers|Clearwater|"
                     r"St\.? Petersburg|Gainesville|Pensacola|Daytona|Destin)\b|\b(?i:located in) 3[234]\d{3}\b")
FEEDS = (("NYT", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
         ("NPR", "https://feeds.npr.org/1001/rss.xml"),
         ("BBC", "https://feeds.bbci.co.uk/news/rss.xml?edition=us"))
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
    return dupont_list(f"https://www.dupontregistry.com/autos/results/{path}")


def dupont_list(url):
    page = get(url)
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


def visible_text(page):
    page = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def wing_evidence(text):
    m = WING.search(NOT_WING.sub(" ", text))
    return m.group(0) if m else None


def dupont_z06():
    """Used C8 Z06 (MY2023+), over MIN_MILES, no Z07 / high wing. Sorted by mileage, highest
    first, so paging stops as soon as a page reaches cars at or under MIN_MILES."""
    base = "https://www.dupontregistry.com/used-chevrolet-corvette__z06-for-sale?year=2023-2035&sort=mileage_desc"
    rows, seen, n_seen = [], set(), 0
    for page_no in range(1, 6):
        items = dupont_list(f"{base}&page={page_no}")
        if not items:
            if page_no == 1:
                raise RuntimeError("duPont Z06 results page lists no cars (URL or markup changed?)")
            break
        low = False
        for i in items:
            vin = i.get("vehicleIdentificationNumber", "")
            if vin in seen:
                continue
            seen.add(vin)
            n_seen += 1
            year = int(i.get("vehicleModelDate") or 0)
            miles = (i.get("mileageFromOdometer") or {}).get("value") or 0
            offer = i.get("offers") or {}
            price = offer.get("price") or 0
            addr = (offer.get("seller") or {}).get("address") or {}
            tag = f"duPont Z06 {year} {vin} {miles}mi ${price} {addr.get('addressRegion', '')}"
            if miles <= MIN_MILES:
                low = True
                continue
            if year < 2023 or "Z06" not in (i.get("model") or i.get("name") or "") or not vin.startswith("1G1Y"):
                print(f"{tag}: drop, not a C8 Z06", file=sys.stderr)
                continue
            if addr.get("addressRegion") == "FL":
                print(f"{tag}: drop, Florida", file=sys.stderr)
                continue
            wing = wing_evidence(i.get("name", "") + " " + visible_text(get(i["url"])))
            if wing:
                print(f"{tag}: drop, wing ({wing})", file=sys.stderr)
                continue
            print(f"{tag}: keep", file=sys.stderr)
            city = ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion")) if x)
            rows.append({"vin": vin, "car": f"{year} Corvette Z06", "color": i.get("color") or "—",
                         "miles": f"{round(miles / 1000)}k", "price": money(price) if price else "Call for price",
                         "seller": (offer.get("seller") or {}).get("name", ""), "location": city,
                         "href": i["url"], "source": "duPont Registry"})
        if low:
            break
    print(f"duPont Z06: {n_seen} MY2023+ listings read, {len(rows)} kept", file=sys.stderr)
    return rows


def bat_z06(row, title, blurb):
    """Check a BaT C8 Z06 auction's own write-up (not the comments) for the wing; fill in
    VIN and location. Returns the row, or None when it's out."""
    try:
        page = get(row["href"])
    except Exception as e:  # keep it on the title check alone
        print(f"BaT Z06 {row['href']}: listing page failed ({e}), title check only", file=sys.stderr)
        page = ""
    m = re.search(r'class="post-excerpt".*?</div>', page, re.S)
    d = re.search(r"<strong>Listing Details</strong>(.*?)</ul>", page, re.S)
    text = " ".join([title, blurb, visible_text(m.group(0)) if m else "", visible_text(d.group(1)) if d else ""])
    wing = wing_evidence(text)
    if wing:
        print(f"BaT Z06 {title}: drop, wing ({wing})", file=sys.stderr)
        return None
    if row["miles"] == "—" and d:
        mi = re.search(r"([\d,]+)k? Miles Shown", visible_text(d.group(1)))
        if mi:
            n = int(mi.group(1).replace(",", "")) * (1000 if "k Miles" in mi.group(0) else 1)
            if n <= MIN_MILES:
                print(f"BaT Z06 {title}: drop, {n} miles", file=sys.stderr)
                return None
            row["miles"] = f"{round(n / 1000)}k"
    vin = re.search(r"Chassis:\s*(?:<[^>]+>)?\s*(1G1Y[A-Z0-9]{13})", d.group(1)) if d else None
    if vin:
        row["vin"] = vin.group(1)
    loc = re.search(r"<strong>Location</strong>:\s*<a[^>]*>(.*?)</a>", page, re.S)
    if loc:
        place = re.sub(r"\s+\d{5}(-\d{4})?$", "", html.unescape(loc.group(1)).strip())
        row["location"] = re.sub(r",\s*Florida$", ", FL", place)
    print(f"BaT Z06 {title}: keep ({row.get('location') or 'location unknown'})", file=sys.stderr)
    return row


def cf_miles(text, labelled_only=False):
    """Odometer from free text. A labelled "Mileage: 3300" wins; otherwise the first "N miles"
    that isn't warranty / service-interval / tire talk."""
    m = re.search(r"\b(?:mileage|miles|odometer|odo)\s*[:=]\s*([\d,.]+)\s*(k)?", text, re.I)
    if not m and not labelled_only:
        for m in re.finditer(r"(?<![$\d,.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d)?)\s*(k)?\s*(?:[a-z]+ ){0,2}(?:miles?|mi)\b", text, re.I):
            before = text[max(0, m.start() - 40):m.start()].lower()
            if not re.search(r"warrant|\byrs?\b|years?|until|valid|every|interval|tires?|wheels|pads|oil|service", before):
                break
        else:
            m = None
    if not m:
        return None
    n = float(m.group(1).replace(",", "") or 0)
    return int(n * 1000) if m.group(2) else int(n)


def cf_price(text):
    """The seller's ask: an "asking / price" amount first, else the first $ figure that isn't
    MSRP or sticker. 40k-400k only, so years and option prices don't count."""
    amt = r"\$?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}(?:\.\d+)?\s?[kK]\b|\d{5,6}\b)"
    tries = [rf"(?:asking|price|selling for|sell for|obo)\D{{0,12}}?{amt}", rf"\${amt[3:]}"]
    for n_try, rx in enumerate(tries):
        for m in re.finditer(rx, text, re.I):
            before = text[max(0, m.start() - (12 if n_try == 0 else 30)):m.start()] + m.group(0)[:-len(m.group(1))]
            if re.search(r"msrp|sticker|total|original|options?|\+|was", before, re.I):
                continue
            raw = m.group(1).replace(",", "").replace(" ", "")
            n = float(raw[:-1]) * 1000 if raw[-1] in "kK" else float(raw)
            if 40000 <= n <= 400000:
                return int(n)
    return None


def cf_place(title, text):
    m = re.search(r"\b(?i:location|located in|car is in|based in)\s*[:-]?\s*([A-Z][\w .'-]{2,40}?(?:,\s*[A-Z][A-Za-z]+)?)(?=[.\n,;!]|\s{2}|\s+\d{5}|$)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"([A-Z][a-z]+(?: [A-Z][a-z]+)*,\s*(?:[A-Z]{2}|[A-Z][a-z]+(?: [A-Z][a-z]+)?))\s*$", title)
    return m.group(1) if m else ""


def corvetteforum_z06():
    """Owner listings in CorvetteForum's C8 classifieds: 2023+ Z06, over MIN_MILES, no wing,
    not Florida. Titles are free text, so anything the title can't settle is read from the
    thread's first post. Prices are the seller's ask."""
    threads, n_z06 = [], 0
    for page_no in range(1, CF_PAGES + 1):
        page = get(CF_FORUM + (f"index{page_no}.html" if page_no > 1 else ""))
        rows = re.findall(r'<div class="trow text-center">(.*?)(?=<div class="trow text-center">|$)', page, re.S)
        found = 0
        for row in rows:
            t = re.search(r'<a href="([^"]+)" id="thread_title_(\d+)"[^>]*>(.*?)</a>', row, re.S)
            if not t:
                continue
            found += 1
            title = html.unescape(re.sub(r"<[^>]+>", "", t.group(3))).strip()
            if not re.search(r"\bZ06\b", title, re.I) or any(t.group(2) == x[1] for x in threads):
                continue
            icon = re.search(r"statusicon/(\w+)", row)
            who = re.search(r"members/[^']+', '_self'\)\">([^<]+)<", row)
            threads.append((t.group(1), t.group(2), title, icon.group(1) if icon else "", who.group(1).strip() if who else ""))
        if not found:
            if page_no == 1:
                raise RuntimeError("CorvetteForum index lists no threads (blocked or markup changed?)")
            break
        time.sleep(CF_PAUSE)
    rows, fetched, failed = [], 0, 0
    for url, tid, title, icon, who in threads:
        n_z06 += 1
        tag = f"CorvetteForum {tid} {title[:70]!r}"
        year = re.search(r"\b(20[2-3]\d)\b|'(2[0-9])\b", title)
        year = int(year.group(1) or "20" + year.group(2)) if year else None
        miles = cf_miles(title)
        title_wing = wing_evidence(title) or (re.search(r"\baero\b", NOT_WING.sub(" ", title), re.I) or [None])[0]
        why = ("want-to-buy / trade / sold" if CF_SKIP.search(title) else "thread locked or moved" if re.search("lock|moved", icon)
               else f"MY{year}" if year and year < 2023 else f"wing ({title_wing})" if title_wing
               else f"{miles} miles" if miles is not None and miles <= MIN_MILES
               else "Florida" if FLORIDA.search(title) else None)
        if why:
            print(f"{tag}: drop, {why}", file=sys.stderr)
            continue
        if fetched >= CF_THREADS:
            print(f"{tag}: not read, thread cap {CF_THREADS} reached", file=sys.stderr)
            continue
        fetched += 1
        time.sleep(CF_PAUSE)
        try:
            page = get(url)
        except Exception as e:
            failed += 1
            print(f"{tag}: drop, thread fetch failed ({e})", file=sys.stderr)
            continue
        post = re.search(r'<div id="post_message_\d+">(.*?)<!-- / message -->', page, re.S)
        text = re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", "\n", post.group(1))))) if post else ""
        text = re.sub(r"\s*\n\s*", "\n", re.sub(r"<script.*?</script>|<noscript.*?</noscript>", " ", text, flags=re.S)).strip()
        if not text:
            print(f"{tag}: drop, first post not found", file=sys.stderr)
            continue
        if year is None:
            y = re.search(r"\b(20[2-3]\d)\b", text)
            year = int(y.group(1)) if y else None
        if miles is None:
            miles = cf_miles(text, labelled_only=False)
        wing = wing_evidence(text)
        why = ("marked sold / deleted in the post" if re.match(r"(?i)\W*(sold|delete[d]?|no longer available|off the market)\b", text)
               else "year not stated" if year is None else f"MY{year}" if year < 2023
               else f"wing ({wing})" if wing else "Florida" if FLORIDA.search(text)
               else "mileage not stated" if miles is None else f"{miles} miles" if miles <= MIN_MILES else None)
        if why:
            print(f"{tag}: drop, {why}", file=sys.stderr)
            continue
        price = cf_price(title) or cf_price(text)
        vin = re.search(r"\b1G1Y[A-HJ-NPR-Z0-9]{13}\b", text)
        print(f"{tag}: keep, MY{year} {miles} mi ask {price} VIN {vin.group(0) if vin else '-'}", file=sys.stderr)
        row = {"car": f"{year} Corvette Z06", "color": "—", "miles": f"{round(miles / 1000)}k",
               "price": money(price) if price else "Ask seller", "seller": who or "Forum seller",
               "location": cf_place(title, text), "href": url, "source": "CorvetteForum"}
        if vin:
            row["vin"] = vin.group(0)
        rows.append(row)
    if fetched and failed == fetched:
        raise RuntimeError(f"CorvetteForum: all {fetched} thread fetches failed")
    print(f"CorvetteForum: {n_z06} Z06 threads on {CF_PAGES} index pages, {fetched} opened, {len(rows)} kept", file=sys.stderr)
    return rows


def bat_live():
    page = get("https://bringatrailer.com/auctions/")
    data = json.loads(re.search(r"var auctionsCurrentInitialData = (\{.*?\});\s*\n", page, re.S).group(1))
    spiders, nsx, z06 = [], [], []
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
        elif re.search(r"\b20(2[3-9]|3\d) Chevrolet Corvette Z06\b", title) and over:
            row = bat_z06(row, title, blurb)
            if row:
                z06.append(row)
    return spiders, nsx, z06


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

    news = attempt("news", headlines, prev.get("news"), stale)
    w = attempt("weather", weather, prev.get("weather"), stale)
    s = attempt("stock", stock, prev.get("stock"), stale)
    prev_rows = {r.get("source"): [] for r in prev.get("cars", []) if r.get("source")}
    for r in prev.get("cars", []):
        if r.get("source"):
            prev_rows[r["source"]].append(r)
    keep = lambda *src: [r for x in src for r in prev_rows.get(x, [])]
    spider = attempt("cavallino", cavallino_458, keep("Cavallino Drift"), stale)
    is_z06 = lambda r: "Z06" in r.get("car", "")
    nsx = attempt("dupont", dupont_nsx, [r for r in keep("duPont Registry") if not is_z06(r)], stale)
    z06 = attempt("dupont_z06", dupont_z06, [r for r in keep("duPont Registry") if is_z06(r)], stale)
    cf = attempt("corvetteforum", corvetteforum_z06, keep("CorvetteForum"), stale)
    bat = attempt("bat", bat_live, None, stale)
    bat_s, bat_n, bat_z = bat if bat else ([], [], [])
    if bat is None:
        for r in keep("BaT"):
            (bat_z if is_z06(r) else bat_n if "NSX" in r["car"] else bat_s).append(r)

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

    nsx_rows, spider_rows, z06_rows = finish(nsx + bat_n), finish(spider + bat_s), finish(z06 + cf + bat_z)
    cars = []
    if nsx_rows:
        cars += [{"group": "Acura NSX-T"}] + nsx_rows
    if spider_rows:
        cars += [{"group": "Ferrari 458 Spider"}] + spider_rows
    cars += [{"group": "Chevrolet Corvette Z06"}] + z06_rows  # header even when empty: the watch is on

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
    for r in nsx_rows + spider_rows + z06_rows:
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
    items = (picks + heads)[:5]  # AAPL already sits in the strip above

    out = {"updated": now.isoformat(timespec="minutes"), "date": today,
           "date_line": now.strftime("%A · %B %-d %Y"), "weather": w, "stock": s, "cars": cars,
           "items": items, "news": news, "alerts": alerts,
           "papers_seen": sorted(seen_papers), "papers_new": papers_new, "stale": stale, "first_seen": seen}
    if len(stale) == 10:  # one per attempt() source above
        print("every source failed; leaving live.json alone", file=sys.stderr)
        return
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
