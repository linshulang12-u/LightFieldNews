#!/usr/bin/env python3
"""Build LightFieldNews.html: the site with all issue data embedded, so it opens by double-click
(no local web server needed). Run after every crawl.

    python scraper/build_standalone.py                                   # thumbnails from ./figs, ./covers
    python scraper/build_standalone.py --asset-base https://<user>.github.io/LightFieldNews/
        # thumbnails load from the published site, so the single file works anywhere (sent by e-mail etc.)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(root: Path = ROOT, asset_base: str = "") -> Path:
    data = root / "data"
    index = json.loads((data / "issues" / "index.json").read_text(encoding="utf-8"))
    meetings_path = data / "meetings.json"
    meetings = json.loads(meetings_path.read_text(encoding="utf-8")) if meetings_path.exists() else {"meetings": []}
    issues = {}
    for meta in index.get("issues", []):
        p = data / "issues" / f"{meta['id']}.json"
        if p.exists():
            issues[meta["id"]] = json.loads(p.read_text(encoding="utf-8"))
    payload = json.dumps({"index": index, "meetings": meetings, "issues": issues}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")            # never close the <script> early
    html = (root / "index.html").read_text(encoding="utf-8")
    marker = "<!--FEMTO_DATA-->"
    if marker not in html:
        raise SystemExit("index.html has no <!--FEMTO_DATA--> marker")
    out = root / "LightFieldNews.html"
    base = json.dumps(asset_base.rstrip("/") + "/" if asset_base else "")
    out.write_text(html.replace(marker, f"<script>window.FEMTO_ASSET_BASE = {base}; window.FEMTO_DATA = {payload};</script>", 1), encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--asset-base", default="", help="URL prefix for figs/ and covers/ (the published site)")
    a = ap.parse_args()
    out = build(a.root, a.asset_base)
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB)")
