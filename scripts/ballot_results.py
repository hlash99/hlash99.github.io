#!/usr/bin/env python3
"""Nov 3 2026 results for Hassan's ballot as a Morning Brief table section (JSON on stdout).

Sources: the CA Secretary of State's statewide feed and San Mateo County's live results page.
Usage: python3 -I ballot_results.py > section.json      (brief table section)
       python3 -I ballot_results.py --csv ballot.csv   (flat file the vote-guide sheet reads with IMPORTDATA)
"""
import csv
import html
import json
import re
import sys
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36"
SOS = "https://api.sos.ca.gov/returns/ballot-measures"
SOS_PAGE = "https://electionresults.sos.ca.gov/returns/ballot-measures"
COUNTY = "https://www.livevoterturnout.com/ENR/sanmateocaenr/20/en/Y3Mvf_Index_20.html"
SHORT = {"1": "Housing bond", "2": "Rainy day fund", "3": "Extend high-income tax", "4": "Public campaign funding",
         "5": "Recall process", "37": "Middle-income home loans", "38": "Immunology research bonds",
         "39": "Voter ID", "40": "One-time billionaire tax", "41": "Limits taxes outside cap",
         "42": "No new property / retro taxes", "43": "Limits local tax measures", "44": "Clinic 90% spending rule",
         "45": "Environmental review changes"}
LOCAL = (("City of Menlo Park, Measure P", "Measure P · Menlo Park initiative"),
         ("County of San Mateo, Measure L", "Measure L · County: extreme weather"),
         ("County of San Mateo, Measure AA", "Measure AA · County: rights"),
         ("County of San Mateo, Measure J", "Measure J · County: vacancies"),
         ("County of San Mateo, Measure U", "Measure U · County: redistricting"),
         ("Regional Transit Measure", "RTM · Regional transit tax"))


def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read().decode("utf-8", "ignore")


def status(yes, no, need):
    total = yes + no
    if not total:
        return "No votes yet", 0.0
    share = yes / total
    return ("Passing" if share > need else "Failing"), share * 100


def main():
    rows, notes, recs = [], [], []
    try:
        d = json.loads(get(SOS))
        rows.append({"group": "California"})
        for m in d["ballot-measures"]:
            n = str(int(m["Number"]))
            st = "Passing" if float(m["yesPercent"]) > 50 else "Failing"
            recs.append((n, f"Prop {n}", float(m["yesPercent"]) / 100, float(m["noPercent"]) / 100, st,
                         int(m.get("yesVotes") or 0), int(m.get("noVotes") or 0)))
            rows.append({"id": f"prop-{n}", "href": SOS_PAGE,
                         "cells": [f"Prop {n} · {SHORT.get(n, m['Name'][:28])}", f"{float(m['yesPercent']):.1f}%",
                                   f"{float(m['noPercent']):.1f}%", st]})
        notes.append(f"State: {d.get('Reporting', '').split(' precincts')[0]} of precincts, {d.get('ReportingTime', '').split(', ')[-1]}")
    except Exception as e:
        notes.append(f"State results unavailable ({type(e).__name__})")
    try:
        page = get(COUNTY)
        upd = re.search(r"Updated:\s*(?:<[^>]+>\s*)*([0-9/]+ [0-9:]+ [AP]M)", page)
        local = []
        for contest, label in LOCAL:
            m = re.search(r"Contest: " + re.escape(contest) + r" \((Majority|55 Percent|2/3) Approval Required\).*?(\[\[.*?\]\]);", page, re.S)
            if not m:
                continue
            need = {"Majority": .5, "55 Percent": .55, "2/3": 2 / 3}[m.group(1)]
            counts = {r[0]: r[-1] for r in json.loads(m.group(2).replace("'", '"'))}
            yes, no = counts.get("YES", 0), counts.get("NO", 0)
            st, share = status(yes, no, need)
            if need != .5:
                st += f" (needs {round(need * 100)}%)"
            recs.append((label.split(" ·")[0].replace("Measure ", ""), label.split(" ·")[0], share / 100,
                         (100 - share) / 100 if yes + no else 0, st, yes, no))
            local.append({"id": re.sub(r"\W+", "-", label.split(" ·")[0].lower()), "href": COUNTY,
                          "cells": [label, f"{share:.1f}%", f"{100 - share:.1f}%" if yes + no else "—", st]})
        if local:
            rows += [{"group": "Menlo Park & San Mateo County"}] + local
        if upd:
            notes.append(f"County: updated {upd.group(1)}")
    except Exception as e:
        notes.append(f"County results unavailable ({type(e).__name__})")
    note = "; ".join(notes) + ". Not final: mail ballots postmarked by Nov 3 count through Nov 10."
    if "--csv" in sys.argv:
        if not recs:
            sys.exit("no results parsed; leaving the CSV alone")
        with open(sys.argv[sys.argv.index("--csv") + 1], "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["key", "measure", "yes_share", "no_share", "status", "yes_votes", "no_votes", "as_of"])
            for r in recs:
                w.writerow([*r[:2], f"{r[2]:.4f}", f"{r[3]:.4f}", *r[4:], note])
        return
    json.dump({"title": "Ballot results", "id": "ballot-results",
               "note": note,
               "table": {"columns": [{"label": "Measure"}, {"label": "Yes", "num": True, "nowrap": True},
                                     {"label": "No", "num": True, "nowrap": True}, {"label": "Status", "nowrap": True}],
                         "rows": rows}}, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
