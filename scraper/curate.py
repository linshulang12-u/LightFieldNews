#!/usr/bin/env python3
"""Curation helper: Feature Articles and Feature of the Week.

Each weekly issue carries 10–15 Feature Articles ("pick": true, with a one- or two-sentence
"why") and one Feature of the Week ("feature": true, also a pick). Papers arrive daily, so
curation is incremental:

    python scraper/curate.py status                      # current picks of the latest issue
    python scraper/curate.py candidates --new-only       # papers not yet reviewed, compact text
    python scraper/curate.py apply selections.json       # write picks / feature, enforce the cap

selections.json
    {
      "picks":   [{"id": "<article id>", "why": "One or two sentences on what is new."}, ...],
      "feature": {"id": "<article id>", "why": "..."}      (optional; may be one of the picks)
      "st_feature": {"id": "<article id>", "why": "..."}   (optional, sites with a column: the column's Pick of the Week)
      "unpick":  ["<article id>", ...]                       (optional)
      "notable": ["<article id>", ...]                       (optional: shown in "Selected Papers"; picks are always notable)
      "add":     [{"title":…, "doi":…, "url":…, "journal":…, "date":"YYYY-MM-DD", "authors":[…], "abstract":…}, ...]
                                                             (optional: papers found by hand, e.g. technique papers the
                                                              crawler missed; added to the issue, reviewed, notable)
      "remove":  ["<article id>", ...]                       (optional: routine / off-topic papers; they move to
                                                              the issue's "removed" list and are never re-added)
      "reviewed_all": true                                   (mark every listed candidate as reviewed)
    }

Cap rule: an issue keeps at most --max-picks (default 15) Feature Articles. When new picks push
it over, the OLDEST picks (earliest "pick_date") are dropped first; the Feature of the Week is
never dropped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SGT = dt.timezone(dt.timedelta(hours=8))


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def issue_path(root: Path, issue_id: str | None) -> Path:
    idx = load(root / "data" / "issues" / "index.json")
    if not idx["issues"]:
        sys.exit("no issues yet")
    iid = issue_id or idx["issues"][0]["id"]
    return root / "data" / "issues" / f"{iid}.json"


def sort_articles(arts: list[dict]) -> None:
    arts.sort(key=lambda a: (bool(a.get("feature")), bool(a.get("st_feature")), bool(a.get("pick")), a.get("pick_date", ""), a.get("date", "")), reverse=True)


def cmd_status(args):
    p = issue_path(Path(args.root), args.issue)
    iss = load(p)
    picks = [a for a in iss["articles"] if a.get("pick") or a.get("feature")]
    unrev = sum(1 for a in iss["articles"] if not a.get("reviewed") and not a.get("pick"))
    nn = sum(1 for a in iss["articles"] if a.get("tier") == "notable")
    print(f"Issue {iss['id']} (No. {iss['number']}): {len(iss['articles'])} papers, {len(picks)} Feature Articles, {nn} selected (notable), {unrev} not yet reviewed")
    for a in sorted(picks, key=lambda a: (not a.get("feature"), a.get("pick_date", ""))):
        flag = "★ FOTW" if a.get("feature") else "  pick"
        print(f"{flag}  {a.get('pick_date', '?'):10}  {a['id']}\n        {a['title'][:110]}\n        why: {a.get('why', '')[:160]}")


def cmd_candidates(args):
    p = issue_path(Path(args.root), args.issue)
    iss = load(p)
    cands = [a for a in iss["articles"] if not a.get("pick") and not a.get("feature")]
    if args.new_only:
        cands = [a for a in cands if not a.get("reviewed")]
    if args.format == "json":
        json.dump([{k: a.get(k) for k in ("id", "date", "title", "journal", "institutions", "categories", "oa", "summary")} for a in cands], sys.stdout, ensure_ascii=False, indent=1)
        return
    print(f"# Issue {iss['id']} · {len(cands)} candidates" + (" (new only)" if args.new_only else ""))
    for a in cands:
        s = (a.get("summary") or "").replace("\n", " ")
        if len(s) > args.max_chars:
            s = s[: args.max_chars].rsplit(" ", 1)[0] + " …"
        print(f"\n[{a['id']}] {a.get('date', '')} · {a.get('journal', '')}" + (" · OA" if a.get("oa") else ""))
        print(f"  {a['title']}")
        if a.get("institutions"):
            print(f"  inst: {', '.join(a['institutions'][:3])}")
        print(f"  cats: {', '.join(a.get('categories', []))}")
        if s:
            print(f"  {s}")


def add_articles(iss: dict, items: list[dict], cfg: dict | None) -> list[str]:
    """Add hand-found papers. Returns the ids added (existing DOIs/ids are skipped)."""
    import news_scraper as vs
    cfg = cfg or vs.load_config(Path(__file__).resolve().parent / "config.yaml")
    have_ids = {a["id"] for a in iss["articles"]}
    have_doi = {(a.get("doi") or "").lower() for a in iss["articles"] if a.get("doi")}
    added = []
    for it in items:
        authors = it.get("authors") or []
        if isinstance(authors, str):
            authors = [x.strip() for x in authors.split(",") if x.strip()]
        rec = {"title": vs.clean_text(it["title"]), "doi": vs.norm_doi(it.get("doi")), "url": it.get("url", ""),
               "journal": it.get("journal", ""), "date": it.get("date", ""), "authors": authors,
               "abstract": it.get("abstract", ""), "institutions_direct": it.get("institutions") or [],
               "oa": bool(it.get("oa", True)), "source": "editor"}
        art = vs.to_article(rec, cfg)
        if art["id"] in have_ids or (art["doi"] and art["doi"] in have_doi):
            continue
        art.update(tier="notable", reviewed=True)
        if it.get("why"):
            art["why"] = it["why"]
        iss["articles"].append(art)
        iss["removed"] = [r for r in iss.get("removed", []) if r != art["id"]]
        have_ids.add(art["id"]); added.append(art["id"])
    return added


def apply_selections(iss: dict, sel: dict, today: str, max_picks: int = 15, cfg: dict | None = None) -> dict:
    added = add_articles(iss, sel["add"], cfg) if sel.get("add") else []
    by_id = {a["id"]: a for a in iss["articles"]}
    missing = [x["id"] for x in sel.get("picks", []) if x["id"] not in by_id]
    for key in ("feature", "st_feature"):
        if sel.get(key) and sel[key]["id"] not in by_id:
            missing.append(sel[key]["id"])
    if missing:
        raise SystemExit(f"unknown article id(s): {missing}")

    rm = set(sel.get("remove", [])) - {x["id"] for x in sel.get("picks", [])} - ({sel["feature"]["id"]} if sel.get("feature") else set())
    rm = {r for r in rm if r in by_id and not by_id[r].get("feature")}
    if rm:
        iss["articles"] = [a for a in iss["articles"] if a["id"] not in rm]
        iss["removed"] = sorted(set(iss.get("removed", [])) | rm)
        by_id = {a["id"]: a for a in iss["articles"]}
    if sel.get("reviewed_all"):
        for a in iss["articles"]:
            a["reviewed"] = True
    for aid in sel.get("unpick", []):
        if aid in by_id:
            by_id[aid].update(pick=False, feature=False, st_feature=False)
    for aid in sel.get("notable", []):
        if aid in by_id:
            by_id[aid]["tier"] = "notable"
    for x in sel.get("picks", []):
        a = by_id[x["id"]]
        a["tier"] = "notable"
        if not a.get("pick"):
            a["pick_date"] = today
        a.update(pick=True, reviewed=True)
        if x.get("why"):
            a["why"] = x["why"].strip()
    if sel.get("feature"):
        for a in iss["articles"]:
            a["feature"] = False
        f = by_id[sel["feature"]["id"]]
        if not f.get("pick"):
            f["pick_date"] = today
        f.update(feature=True, pick=True, reviewed=True, tier="notable")
        if sel["feature"].get("why"):
            f["why"] = sel["feature"]["why"].strip()

    if sel.get("st_feature"):
        for a in iss["articles"]:
            a["st_feature"] = False
        f = by_id[sel["st_feature"]["id"]]
        if not f.get("pick"):
            f["pick_date"] = today
        f.update(st_feature=True, pick=True, reviewed=True, tier="notable")
        if sel["st_feature"].get("why"):
            f["why"] = sel["st_feature"]["why"].strip()

    # cap: drop the oldest picks first, never the Feature of the Week (or a column's pick of the week)
    picks = [a for a in iss["articles"] if a.get("pick")]
    dropped = []
    if len(picks) > max_picks:
        removable = sorted([a for a in picks if not a.get("feature") and not a.get("st_feature")], key=lambda a: (a.get("pick_date", ""), a.get("date", "")))
        for a in removable[: len(picks) - max_picks]:
            a["pick"] = False
            dropped.append(a["id"])
    sort_articles(iss["articles"])
    iss["curated"] = today
    return {"added": added, "picks": sum(1 for a in iss["articles"] if a.get("pick")), "dropped": dropped, "removed": len(rm),
            "feature": next((a["id"] for a in iss["articles"] if a.get("feature")), None)}


def cmd_apply(args):
    root = Path(args.root)
    p = issue_path(root, args.issue)
    iss = load(p)
    sel = load(Path(args.selections))
    today = args.today or dt.datetime.now(SGT).date().isoformat()
    res = apply_selections(iss, sel, today, args.max_picks)
    save(p, iss)
    print(json.dumps(res, ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--issue", help="issue id (YYYY-MM-DD); default: latest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    c = sub.add_parser("candidates")
    c.add_argument("--new-only", action="store_true")
    c.add_argument("--max-chars", type=int, default=450)
    c.add_argument("--format", choices=["text", "json"], default="text")
    a = sub.add_parser("apply")
    a.add_argument("selections")
    a.add_argument("--max-picks", type=int, default=15)
    a.add_argument("--today")
    args = ap.parse_args(argv)
    {"status": cmd_status, "candidates": cmd_candidates, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
