# hlash99.github.io

Personal landing page / dashboard at **https://hlash99.github.io** that links to my projects.

The card grid renders immediately from the `MANIFEST` list inside `index.html` (no network
call needed). The GitHub API is only a background check, at most once every 6 hours per
browser, that picks up repos added since - repos with GitHub Pages enabled show up under
**Live sites**, the rest under **Other repositories**. Per-repo icons, blurbs, preview
adapters and stale thresholds (`staleH`, hours) live in the `META` map inside `index.html`;
a card whose data is older than its threshold gets a "stale" flag.
