#!/usr/bin/env python3
"""Server-side data for the hub's Morning Brief panel. Runs in GitHub Actions; no Claude.

Writes live.json: date, weather (NWS), AAPL (stockanalysis.com), car listings (Cavallino
Drift + duPont Registry + Bring a Trailer: black 458 Spider, NSX-T, Red Mist C8 Corvette Z06; the Z06 also
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
# Listings that are not the car their title says. 463983602: CarGurus titled an E-Ray 2LZ convertible
# (VIN 1G1YL3D42R5501246, 6.2L LT2) as a Z06; 461133627 is an E-Ray 3LZ coupe (1G1YM2D43R5200055)
# titled the same way. Matched on the URL without its query string.
NOT_AS_TITLED_URLS = {"https://www.cargurus.com/details/463983602", "https://www.cargurus.com/details/461133627"}
MAX_458, MAX_NSX, MIN_MILES = 300000, 160000, 30000  # 458 and NSX
BRIEF_DAYS = 3  # how long a CarGurus row from brief.json stays on the hub without a fresh brief
Z06_MAX_PRICE = 100000  # Z06: ask (or current bid) under this; any mileage; anywhere in the US but Florida
# Trim and body from the VIN (5th and 6th characters), checked against 47 CarGurus listings on
# 2026-11-20. "L" was not among them and is assumed to pair with K (1LZ) and M (3LZ).
Z06_TRIM_VIN = {"D": "1LZ", "K": "1LZ", "E": "2LZ", "L": "2LZ", "F": "3LZ", "M": "3LZ"}
Z06_BODY_VIN = {"2": "coupe", "3": "convertible"}


def not_z06_vin(vin):
    """True when a VIN is on hand and it is not a C8 Z06. The 8th character is the engine: "3" is
    the Z06's 5.5L LT6; "4" is the 6.2L LT2 of the Stingray and the E-Ray (E-Rays are 1G1YK/YL/YM
    and get listed as Z06s). No VIN settles nothing, so that returns False."""
    vin = (vin or "").upper()
    return len(vin) == 17 and not (vin.startswith("1G1Y") and vin[7] == "3")

# Front lift (RPO E60) is required but rarely itemised: a listing that says it has none is out,
# one that doesn't say gets "Lift unconfirmed".
LIFT = re.compile(r"\bE60\b|\b(?:front|nose)[- ](?:axle[- ])?lift|\blift (?:system|kit)\b|\bfront[- ]end lift", re.I)
NO_LIFT = re.compile(r"\b(?:no|non|without|w/o|lacks?|not equipped with)[- ](?:a |the )?(?:front[- ]|nose[- ])?(?:axle[- ])?lift\b", re.I)
# No accidents: out on positive evidence only ("no accidents reported" is stripped first).
NO_ACCIDENT = re.compile(r"\b(?:no|zero|0|never|free of|without|not)\b[\w ,/-]{0,40}?\b(?:accidents?|damage|collisions?)\b", re.I)
ACCIDENT = re.compile(r"\b(?:accidents?|damage|collision) (?:was |were )?reported\b|\b(?:structural|frame) damage\b|\bsalvage\b"
                      r"|\b(?:rebuilt|branded) title\b|\bprior (?:accident|damage|collision)\b|\baccident history\b"
                      r"|\b(?:lists?|shows?|reports?|indicates?|notes?) (?:an? |one |two |minor |moderate |severe )*(?:accidents?|damage|collision)\b", re.I)
STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO",
          "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
          "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
          "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
          "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
          "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC",
          "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
          "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
          "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
          "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC"}
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
# 458 Spider: black exterior only. The colour must START with Nero / Black (Nero Daytona, Nero DS,
# Nero Stellato, Nero Noctis...), so "Grigio w/ black roof" or "White / Nero" (interior) don't pass.
BLACK = re.compile(r"^\s*(?:(?:gloss|metallic|matte|satin)\s+)?(?:nero|black)\b", re.I)
# C8 Z06: Red Mist exterior only (GM's Red Mist Metallic Tintcoat). Unknown colour is out too.
# Page text only counts when it names Red Mist as the paint, so a "similar cars" strip can't pass a car.
RED_MIST = re.compile(r"\bred[\s-]*mist\b", re.I)
RED_MIST_PAINT = re.compile(r"(?:exterior|ext\.|colou?r|paint(?:ed)?|finished in)\W{0,3}(?:[\w-]+\W{1,3}){0,4}?red[\s-]*mist\b"
                            r"|\bred[\s-]*mist(?:\s+metallic)?\s+(?:tint[\s-]?coat|exterior|paint|over\b)", re.I)
Z06_CHECKED = {}  # source -> listings looked at this run, for the dash's empty-watch tile
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


def exterior(color):
    """Exterior half of an "Exterior / Interior" colour field."""
    return (color or "").split(" / ")[0].strip()


def is_black(color):
    return bool(BLACK.search(exterior(color)))


def is_red_mist(color):
    return bool(RED_MIST.search(exterior(color)))


def z06_paint(color, text=""):
    """Red Mist Z06 paint, or "" when the car isn't (or isn't shown to be) Red Mist. A listed
    colour that names another paint settles it; a blank or bare "Red" falls back to the text."""
    ext = exterior(color)
    if RED_MIST.search(ext):
        return ext
    if ext and not re.fullmatch(r"(?i)[\s\u2014-]*|red|other|unknown|n/?a|not specified", ext):
        return ""
    return "Red Mist" if RED_MIST_PAINT.search(text or "") else ""


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
        if not is_black(c[1]):
            continue  # black 458s only; unknown ("— / —") is out too
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


def z06_spec(vin, text):
    """(trim, body, flags, why) for a C8 Z06. why is set when the car is out: a 1LZ, an accident
    on record, or a listing that says it has no front lift. Trim and body come from the VIN
    first, then the text; what can't be settled becomes a flag."""
    vin = vin or ""
    trim = Z06_TRIM_VIN.get(vin[4:5], "") if vin.startswith("1G1Y") else ""
    if not trim:
        found = {t.upper() for t in re.findall(r"\b[123]LZ\b", text, re.I)}
        trim = found.pop() if len(found) == 1 else ""
    body = Z06_BODY_VIN.get(vin[5:6], "") if vin.startswith("1G1Y") else ""
    if not body:
        body = "convertible" if re.search(r"\bconvertible\b|\bHTC\b", text, re.I) else "coupe" if re.search(r"\bcoupe\b", text, re.I) else ""
    flags = []
    if trim == "1LZ":
        return trim, body, flags, "1LZ"
    hit = ACCIDENT.search(NO_ACCIDENT.sub(" ", text))
    if hit:
        return trim, body, flags, f"accident / damage ({hit.group(0)})"
    if NO_LIFT.search(text):
        return trim, body, flags, "no front lift"
    if not LIFT.search(text):
        flags.append("Lift unconfirmed")
    if not trim:
        flags.append("Trim unconfirmed")
    return trim, body, flags, None


def z06_name(year, trim, body):
    return " ".join(x for x in (str(year), "Corvette Z06", trim, body) if x)


def dupont_z06():
    """Used C8 Z06 (MY2023+) under Z06_MAX_PRICE, any mileage, dealer anywhere in the US but
    Florida, 2LZ / 3LZ, no Z07 / high wing. Cheapest first, so paging stops at the first page
    that is all over the cap. The results URL has no working state filter, so the state is
    read from each listing's seller address."""
    base = "https://www.dupontregistry.com/used-chevrolet-corvette__z06-for-sale?year=2023-2035&sort=price_asc"
    rows, seen, n_seen = [], set(), 0
    for page_no in range(1, 9):
        items = dupont_list(f"{base}&page={page_no}")
        if not items:
            if page_no == 1:
                raise RuntimeError("duPont Z06 results page lists no cars (URL or markup changed?)")
            break
        under = False
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
            state = addr.get("addressRegion") or ""
            tag = f"duPont Z06 {year} {vin} {miles}mi ${price} {state}"
            if not price:  # "Call for price" sorts first; it can't be shown to be under the cap
                under = True
                print(f"{tag}: drop, no price listed", file=sys.stderr)
                continue
            if price >= Z06_MAX_PRICE:
                continue
            under = True
            if year < 2023 or "Z06" not in (i.get("model") or i.get("name") or "") or not vin.startswith("1G1Y"):
                print(f"{tag}: drop, not a C8 Z06", file=sys.stderr)
                continue
            if state == "FL":
                print(f"{tag}: drop, Florida", file=sys.stderr)
                continue
            if state not in STATES.values():
                print(f"{tag}: drop, not in the US or location unknown", file=sys.stderr)
                continue
            if Z06_TRIM_VIN.get(vin[4:5]) == "1LZ":
                print(f"{tag}: drop, 1LZ", file=sys.stderr)
                continue
            if not z06_paint(i.get("color"), "x red mist exterior"):  # a named other paint settles it unread
                print(f"{tag}: drop, not Red Mist ({i.get('color')})", file=sys.stderr)
                continue
            text = visible_text(get(i["url"]))
            wing = wing_evidence(i.get("name", "") + " " + text)
            if wing:
                print(f"{tag}: drop, wing ({wing})", file=sys.stderr)
                continue
            paint = z06_paint(i.get("color"), text)
            if not paint:
                print(f"{tag}: drop, not Red Mist ({i.get('color') or 'colour unknown'})", file=sys.stderr)
                continue
            trim, body, flags, why = z06_spec(vin, text)
            if why:
                print(f"{tag}: drop, {why}", file=sys.stderr)
                continue
            print(f"{tag}: keep, {paint} {trim} {body} {flags}", file=sys.stderr)
            city = ", ".join(x for x in (addr.get("addressLocality"), state) if x)
            rows.append({"vin": vin, "car": z06_name(year, trim, body), "color": paint,
                         "miles": f"{round(miles / 1000)}k", "price": money(price),
                         "seller": (offer.get("seller") or {}).get("name", ""), "location": city,
                         "href": i["url"], "source": "duPont Registry", "flags": flags})
        if not under:
            break
    Z06_CHECKED["duPont"] = n_seen
    print(f"duPont Z06: {n_seen} MY2023+ listings read (cheapest first), {len(rows)} kept", file=sys.stderr)
    return rows


def bat_z06(row, title, blurb):
    """Check a BaT C8 Z06 auction's own write-up (not the comments) for the wing, paint, trim,
    accidents and front lift, and its Location line for a US state other than Florida; fill in
    VIN and miles. Returns the row, or None when it's out."""
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
    items = [visible_text(li).strip() for li in re.findall(r"<li>(.*?)</li>", d.group(1), re.S)] if d else []
    listed = next((m.group(1) for x in items for m in [re.match(r"(.+?)\s+Paint\b(?![- ]?Protection)", x)] if m), "")
    if not listed:
        f = re.search(r"\bfinished in ([\w -]+?)(?: over\b|[,.])", visible_text(m.group(0)) if m else "", re.I)
        listed = f.group(1).strip() if f else ""
    paint = z06_paint(listed, text)
    if not paint:
        print(f"BaT Z06 {title}: drop, not Red Mist ({listed or 'colour unknown'})", file=sys.stderr)
        return None
    row["color"] = paint
    if row["miles"] == "—" and d:
        mi = re.search(r"([\d,]+)k? Miles Shown", visible_text(d.group(1)))
        if mi:
            n = int(mi.group(1).replace(",", "")) * (1000 if "k Miles" in mi.group(0) else 1)
            row["miles"] = f"{round(n / 1000)}k"
    vin = re.search(r"Chassis:\s*(?:<[^>]+>)?\s*(1G1Y[A-Z0-9]{13})", d.group(1)) if d else None
    if vin:
        row["vin"] = vin.group(1)
    loc = re.search(r"<strong>Location</strong>:\s*<a[^>]*>(.*?)</a>", page, re.S)
    place = re.sub(r"\s+\d{5}(-\d{4})?$", "", html.unescape(loc.group(1)).strip()) if loc else ""
    st = re.search(r",\s*([A-Za-z ]+)$", place)
    state = STATES.get(st.group(1).strip()) if st else None
    if not state or state == "FL":
        print(f"BaT Z06 {title}: drop, {'Florida' if state else 'not in the US or location unknown'} ({place})", file=sys.stderr)
        return None
    trim, body, flags, why = z06_spec(row.get("vin"), text)
    if why:
        print(f"BaT Z06 {title}: drop, {why}", file=sys.stderr)
        return None
    year = re.search(r"\b20\d\d\b", title)
    row["car"] = z06_name(year.group(0) if year else "", trim, body)
    row["flags"] = flags
    row["location"] = place[:st.start()] + ", " + state
    print(f"BaT Z06 {title}: keep ({row['location']}) {trim} {body} {flags}", file=sys.stderr)
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
    """Owner listings in CorvetteForum's C8 classifieds: 2023+ Z06 asking under Z06_MAX_PRICE,
    2LZ / 3LZ, no wing, no accidents, any mileage, and the title or first post must name where
    the car is (not Florida). Titles are free text, so anything the title can't settle is read
    from the thread's first post. Prices are the seller's ask."""
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
        title_price = cf_price(title)
        title_wing = wing_evidence(title) or (re.search(r"\baero\b", NOT_WING.sub(" ", title), re.I) or [None])[0]
        why = ("want-to-buy / trade / sold" if CF_SKIP.search(title) else "thread locked or moved" if re.search("lock|moved", icon)
               else f"MY{year}" if year and year < 2023 else f"wing ({title_wing})" if title_wing
               else f"asking {money(title_price)}" if title_price and title_price >= Z06_MAX_PRICE
               else "1LZ" if re.search(r"\b1LZ\b", title, re.I) and not re.search(r"\b[23]LZ\b", title, re.I)
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
        price = title_price or cf_price(text)
        vin = re.search(r"\b1G1Y[A-HJ-NPR-Z0-9]{13}\b", text)
        place = cf_place(title, text)
        trim, body, flags, spec_why = z06_spec(vin.group(0) if vin else "", title + "\n" + text)
        why = ("marked sold / deleted in the post" if re.match(r"(?i)\W*(sold|delete[d]?|no longer available|off the market)\b", text)
               else "year not stated" if year is None else f"MY{year}" if year < 2023
               else f"wing ({wing})" if wing else "Florida" if FLORIDA.search(text)
               else "not Red Mist (or colour not stated)" if not RED_MIST.search(title + "\n" + text)
               else "price not stated" if not price
               else f"asking {money(price)}" if price >= Z06_MAX_PRICE
               else spec_why if spec_why
               else None if place else "location not stated")
        if why:
            print(f"{tag}: drop, {why}", file=sys.stderr)
            continue
        print(f"{tag}: keep, MY{year} {trim} {body} {miles} mi ask {price} ({place}) {flags} VIN {vin.group(0) if vin else '-'}", file=sys.stderr)
        st = re.search(r",\s*([A-Za-z ]+)$", place)  # dash shows the last 2 chars as the state
        if st and st.group(1).strip() in STATES:
            place = place[:st.start()] + ", " + STATES[st.group(1).strip()]
        row = {"car": z06_name(year, trim, body), "color": "Red Mist",
               "miles": f"{round(miles / 1000)}k" if miles is not None else "—",
               "price": money(price), "seller": who or "Forum seller",
               "location": place, "href": url, "source": "CorvetteForum", "flags": flags}
        if vin:
            row["vin"] = vin.group(0)
        rows.append(row)
    if fetched and failed == fetched:
        raise RuntimeError(f"CorvetteForum: all {fetched} thread fetches failed")
    Z06_CHECKED["CorvetteForum"] = n_z06
    print(f"CorvetteForum: {n_z06} Z06 threads on {CF_PAGES} index pages, {fetched} opened, {len(rows)} kept", file=sys.stderr)
    return rows


def brief_z06(today):
    """Z06 rows the Morning Brief found through the CarGurus connector, which this job can't
    call. They ride in on brief.json (scripts/brief_export.py) and count for BRIEF_DAYS days
    after that brief's date; main() screens them like any other row."""
    try:
        b = json.load(open(os.path.join(ROOT, "brief.json")))
    except (OSError, ValueError):
        return []
    age = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(b.get("date") or "2000-01-01", "%Y-%m-%d")).days
    if not 0 <= age <= BRIEF_DAYS:
        print(f"brief.json is {age} days old: its CarGurus rows are not used", file=sys.stderr)
        return []
    rows = []
    for r in b.get("cars") or []:
        if r.get("group") or r.get("source") != "CarGurus" or "Z06" not in r.get("car", "") or not r.get("href"):
            continue
        rows.append({"car": re.sub(r"^(\d{4}) (?=Z06)", r"\1 Corvette ", r["car"]), "color": r.get("color", ""),
                     "miles": r.get("miles", "—"), "price": r.get("price", ""), "seller": r.get("seller", ""),
                     "location": r.get("location", ""), "href": r["href"], "source": "CarGurus",
                     "stated_title": r.get("title", ""),
                     "flags": ([] if re.search(r"lift confirmed", r.get("flag", ""), re.I) else ["Lift unconfirmed"])
                              + ([] if r.get("vin") else ["Z06 unconfirmed (no VIN)"])})
        if r.get("vin"):
            rows[-1]["vin"] = r["vin"]
    print(f"brief.json ({b.get('date')}): {len(rows)} CarGurus Z06 rows", file=sys.stderr)
    return rows


def bat_458(row, title):
    """BaT 458s must be confirmed black: the Listing Details "... Paint" line, else the
    write-up's "finished in ...". Fills colour and miles; None when it's out."""
    try:
        page = get(row["href"])
    except Exception as e:
        print(f"BaT 458 {title}: drop, listing page failed ({e}), colour unknown", file=sys.stderr)
        return None
    d = re.search(r"<strong>Listing Details</strong>(.*?)</ul>", page, re.S)
    items = [visible_text(li).strip() for li in re.findall(r"<li>(.*?)</li>", d.group(1), re.S)] if d else []
    paint = next((m.group(1) for x in items for m in [re.match(r"(.+?)\s+Paint\b(?![- ]?Protection)", x)] if m), "")
    if not paint:
        ex = re.search(r'class="post-excerpt".*?</p>', page, re.S)
        m = re.search(r"\bfinished in ([\w -]+?)(?: over\b|[,.])", visible_text(ex.group(0)) if ex else "", re.I)
        paint = m.group(1).strip() if m else ""
    if not is_black(paint):
        print(f"BaT 458 {title}: drop, exterior {paint or 'unknown'}", file=sys.stderr)
        return None
    mi = next((x for x in items if re.search(r"\bMiles\b", x)), "")
    m = re.match(r"~?([\d,]+)(k)? Miles", mi)
    if m and row["miles"] == "—":
        n = int(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        if n <= MIN_MILES:
            print(f"BaT 458 {title}: drop, {n} miles", file=sys.stderr)
            return None
        row["miles"] = f"{round(n / 1000)}k"
    vin = re.search(r"\bZFF[A-Z0-9]{14}\b", " ".join(items))
    if vin:
        row["vin"] = vin.group(0)
    row["color"] = paint
    print(f"BaT 458 {title}: keep, {paint}", file=sys.stderr)
    return row


def bat_live():
    page = get("https://bringatrailer.com/auctions/")
    data = json.loads(re.search(r"var auctionsCurrentInitialData = (\{.*?\});\s*\n", page, re.S).group(1))
    spiders, nsx, z06, n_z06 = [], [], [], 0
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
            row = bat_458(row, title)
            if row:
                spiders.append(row)
        elif re.search(r"(1997|1998|1999|2000|2001) Acura NSX-T 6-Speed", title) and over and bid <= MAX_NSX:
            nsx.append(row)
        elif re.search(r"\b20(2[3-9]|3\d) Chevrolet Corvette Z06\b", title) and bid < Z06_MAX_PRICE:
            n_z06 += 1
            row = bat_z06(row, title, blurb)
            if row:
                z06.append(row)
    Z06_CHECKED["BaT"] = n_z06
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


PARCEL_DAYS = 3  # how long a delivered parcel stays on the hub
PARCEL_DONE = re.compile(r"\bdelivered\b|\bsigned\b|successful delivery|proof of delivery", re.I)
PARCEL_NOT_DONE = re.compile(r"not (?:been )?delivered|undeliver|attempt|out for delivery|to be delivered|will be delivered", re.I)
PARCEL_PROBLEM = re.compile(r"exception|return|undeliver|fail|attempt|\bheld\b|\bhold\b|detain|unable|refus|"
                            r"insufficient address|incorrect address|notice left|awaiting (?:pickup|collection)|ready for pickup", re.I)


def parcels(prev):
    """Parcels being watched, from 4PX's public tracking endpoint. The PARCELS secret is a JSON
    list of {"name": "...", "code": "<tracking number>"}; this repo is public and a tracking
    number lets anyone open the full record (including a delivery signature), so the number
    never reaches live.json: only the name, the status and the latest scan. Without the secret
    (a local run) the previous list is kept."""
    raw = os.environ.get("PARCELS", "").strip()
    if not raw:
        return prev
    watch = [w for w in json.loads(raw) if w.get("code")]
    body = json.dumps({"queryCodes": [w["code"] for w in watch], "language": "en-us", "translateLanguage": "en-us"}).encode()
    req = urllib.request.Request("https://track.4px.com/track/v2/front/listTrackV3", data=body, headers={
        "User-Agent": UA, "Content-Type": "application/json", "Origin": "https://track.4px.com", "Referer": "https://track.4px.com/"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")).get("data") or []
    by_code = {d.get("queryCode"): d for d in data}
    was = {p.get("name"): p for p in prev or []}
    now = pacific_now()
    out = []
    for w in watch:
        name = str(w.get("name") or "Parcel")[:18]
        tracks = (by_code.get(w["code"]) or {}).get("tracks") or []
        if not tracks:  # nothing back for this one: keep what was known
            if name in was:
                out.append(was[name])
            continue
        t = tracks[0]  # newest first
        desc = re.sub(r"\s+", " ", str(t.get("tkTranslatedDesc") or t.get("tkDesc") or "")).strip().rstrip(".")
        when = now
        try:
            when = datetime.strptime(str(t.get("tkDate"))[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).astimezone(now.tzinfo)
        except ValueError:
            pass
        done = bool(PARCEL_DONE.search(desc)) and not PARCEL_NOT_DONE.search(desc)
        state = ("Delivered" if done else "Out today" if re.search(r"out for delivery", desc, re.I)
                 else "Check" if PARCEL_PROBLEM.search(desc) else "In transit")
        place = re.sub(r"\s+", " ", str(t.get("tkLocation") or "")).split(",")[0].strip()[:24]
        p = {"name": name, "state": state, "event": desc[:80], "place": place,
             "date": when.strftime("%Y-%m-%d"), "carrier": "4PX", "href": "https://track.4px.com/"}
        # The day it first read Delivered, so the hub can drop it PARCEL_DAYS later.
        if done:
            p["delivered"] = (was.get(name) or {}).get("delivered") or p["date"]
        out.append(p)
    return out


def parcel_tile(p):
    day = datetime.strptime(p["date"], "%Y-%m-%d").strftime("%b %-d")
    title = ", ".join(b for b in (p.get("place"), p.get("event")) if b) + " · " + day
    t = tile(title, p.get("href") or "https://track.4px.com/", p.get("carrier", ""), "parcel", p["name"], p["state"],
             "up" if p["state"] == "Delivered" else "down" if p["state"] == "Check" else None)
    t["alert"] = True
    return t


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
            flags += r.get("flags") or []
            clean = r.get("vin") in history.get("_clean_title", [])
            r = dict(r, title="Clean (Carfax)" if clean else r.get("stated_title") or "Title not stated", flag=", ".join(flags))
            out.append(r)
        return out

    nsx_rows, spider_rows, z06_rows = finish(nsx + bat_n), finish(spider + bat_s), finish(z06 + cf + bat_z + brief_z06(today))
    spider_rows = [r for r in spider_rows if is_black(r.get("color"))]  # also screens fallback rows
    first_amount = lambda r: int(re.sub(r"\D", "", str(r.get("price", "")).split(" ")[0]) or 0)
    # Red Mist, under the cap and not a 1LZ: also screens fallback rows kept from an older run.
    z06_rows = [r for r in z06_rows if is_red_mist(r.get("color")) and first_amount(r) < Z06_MAX_PRICE
                and not re.search(r"\b1LZ\b", r.get("car", ""))]
    for r in z06_rows:  # a Z06 by title only: the VIN's engine code decides
        if not_z06_vin(r.get("vin")) or r.get("href", "").split("?")[0] in NOT_AS_TITLED_URLS:
            print(f"Z06 {r.get('href')}: drop, not a Z06 (VIN {r.get('vin') or 'none'})", file=sys.stderr)
    z06_rows = [r for r in z06_rows if not not_z06_vin(r.get("vin"))
                and r.get("href", "").split("?")[0] not in NOT_AS_TITLED_URLS]
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
    # A car alert lives only while its car is still on the list: a listing that is gone, or that
    # a filter now rejects (non-black 458, Z06 over the price cap), stops surfacing at once.
    shown = {r.get("vin") or r["href"] for r in nsx_rows + spider_rows + z06_rows}
    car_key = lambda k: k[4:] if k.startswith("car:") else k[5:].rsplit(":", 1)[0] if k.startswith("drop:") else None
    alerts = {k: v for k, v in alerts.items() if v.get("kind") != "car" or car_key(k) in shown}
    picks = [tile(v["title"], v["href"], v.get("source", ""), v.get("kind", "you"), v.get("label"), v.get("stat") or "New", v.get("dir"))
             for v in sorted(alerts.values(), key=lambda a: a["date"], reverse=True)]
    for p in picks:
        p["alert"] = True
    # Watched parcels lead until PARCEL_DAYS after delivery. Not counted in `stale`: the list is
    # optional and a 4PX outage shouldn't mark the whole brief stale.
    try:
        watched = parcels(prev.get("parcels")) or []
    except Exception as e:
        print(f"parcels failed: {e}", file=sys.stderr)
        watched = prev.get("parcels") or []
    gone = (now - timedelta(days=PARCEL_DAYS)).strftime("%Y-%m-%d")
    watched = [p for p in watched if not p.get("delivered") or p["delivered"] >= gone]
    picks = [parcel_tile(p) for p in watched] + picks
    heads = [tile(n["title"], n["href"], n.get("source", "")) for n in (news or [])]
    items = (picks + heads)[:5]  # AAPL already sits in the strip above

    out = {"updated": now.isoformat(timespec="minutes"), "date": today,
           "date_line": now.strftime("%A · %B %-d %Y"), "weather": w, "stock": s, "cars": cars,
           "items": items, "news": news, "alerts": alerts,
           "z06_watch": {"checked": sum(dict((prev.get("z06_watch") or {}).get("by_source") or {}, **Z06_CHECKED).values()),
                         "by_source": dict((prev.get("z06_watch") or {}).get("by_source") or {}, **Z06_CHECKED),
                         "label": f"Red Mist Z06 · <${Z06_MAX_PRICE // 1000}k · lift",
                         "sources": ["duPont", "BaT", "CorvetteForum", "CarGurus"]},
           "papers_seen": sorted(seen_papers), "papers_new": papers_new, "parcels": watched,
           "stale": stale, "first_seen": seen}
    if len(stale) == 10:  # one per attempt() source above
        print("every source failed; leaving live.json alone", file=sys.stderr)
        return
    with open(OUT, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
