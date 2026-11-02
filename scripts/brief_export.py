#!/usr/bin/env python3
"""Turn a full Morning Brief JSON into the small public brief.json the hub shows.

Usage: python3 -I scripts/brief_export.py <full-brief.json> brief.json

This repo is public, so everything here is an allowlist: a field reaches
brief.json only if this file copies it by name. Never commit the full brief.
"""
import json
import sys
from datetime import datetime, timedelta, timezone

# Personal sections stay out until Hassan approves them.
INCLUDE_NEEDS_ATTENTION = False
INCLUDE_COMING_UP = False

BLOCKED_HOSTS = ("mail.google.com", "mail.yahoo.com", "claude.ai")


def text(v, limit=120):
    return str(v or "").strip()[:limit]


def public_href(u):
    u = str(u or "").strip()
    if not u.startswith("https://") or any(h in u for h in BLOCKED_HOSTS):
        return ""
    return u[:300]


def weather(w):
    if not w:
        return None
    return {k: w.get(k) for k in ("high", "low", "icon", "summary") if w.get(k) is not None}


def stock(s):
    if not s:
        return None
    out = {k: text(s.get(k), 24) for k in ("symbol", "price", "change_pct", "dir", "session") if s.get(k)}
    a = s.get("after") or {}
    if a.get("price"):
        out["after"] = {k: text(a.get(k), 24) for k in ("label", "price", "change_pct", "dir") if a.get(k)}
    return out


def cars(sections):
    sec = next((s for s in sections if s.get("table") and text(s.get("title")).startswith("Car listings")), None)
    if not sec:
        return []
    rows = []
    for r in sec["table"].get("rows", []):
        if r.get("group") is not None and not r.get("cells"):
            rows.append({"group": text(r["group"], 40)})
            continue
        c = list(r.get("cells") or []) + [""] * 9
        rows.append({
            "car": text(c[0], 60), "color": text(c[1], 40), "miles": text(c[2], 12), "price": text(c[3], 40),
            "title": text(c[4], 24), "seller": text(c[5], 40), "location": text(c[6], 30),
            "flag": text(r.get("flag"), 30), "href": public_href(r.get("href")),
        })
    return rows


def coming_up(sections):
    sec = next((s for s in sections if s.get("timeline")), None)
    out = []
    for stop in (sec or {}).get("timeline", [])[:7]:
        evs = [text(e.get("text") if isinstance(e, dict) else e, 50) for e in stop.get("events", [])]
        out.append({"date": text(stop.get("date"), 10), "label": text(stop.get("label"), 16),
                    "accent": bool(stop.get("accent")), "events": evs[:4]})
    return out


def main(src, dst):
    b = json.load(open(src))
    b = b.get("data", b) if isinstance(b.get("data"), dict) else b
    pt = timezone(timedelta(hours=-8))
    out = {
        "updated": datetime.now(timezone.utc).astimezone(pt).isoformat(timespec="minutes"),
        "date": text(b.get("date"), 10),
        "date_line": text(b.get("date_line"), 40),
        "weather": weather(b.get("weather")),
        "stock": stock(b.get("stock")),
        "cars": cars(b.get("sections") or []),
    }
    if INCLUDE_NEEDS_ATTENTION:
        out["needs_attention"] = [text(i.get("title"), 80) for i in b.get("needs_attention") or []][:5]
    if INCLUDE_COMING_UP:
        out["coming_up"] = coming_up(b.get("sections") or [])
    with open(dst, "w") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
