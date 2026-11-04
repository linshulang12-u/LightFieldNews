#!/usr/bin/env python3
"""LightFieldNews weekly scraper (adapted from VibraNews by ForestBin-Raman).

Finds new ultrafast-laser fabrication papers (hard & brittle materials, laser-written waveguides,
two-photon polymerization, optical computing and storage, surface structuring, fibre gratings,
selective etching, welding, bulk modification, beam shaping, bursts ...) published in a date
window, and writes one issue file:

    data/issues/<issue-date>.json      the issue (articles, categories, figures)
    data/issues/index.json             list of all issues, newest first
    figs/<article-id>.jpg              open-access papers: the article page's preview image
                                       (og:image / twitter:image, usually Fig. 1 or the
                                       graphical abstract)
    covers/<journal>.jpg               paywalled papers (or no preview): the journal cover

Sources: Europe PMC, Crossref, arXiv (free APIs, no key needed).

Usage
    python scraper/news_scraper.py --daily             # daily: last 3 days into this week's issue
    python scraper/news_scraper.py                     # weekly: issue for this Monday, previous 7 days
    python scraper/news_scraper.py --issue-date 2026-11-09
    python scraper/news_scraper.py --since 2026-11-02 --until 2026-11-08
    python scraper/news_scraper.py --no-figures --dry-run
    python scraper/news_scraper.py --refresh-figures --issue-date 2026-11-02   # thumbnails for an existing issue

Re-running for an existing issue keeps everything the editor changed
(summaries, picks, Feature of the Week, deletions are NOT re-added if listed
in the issue's "removed" list) and only adds papers that are new.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import json
import logging
import re
import sys
import time
import urllib.robotparser
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup
from PIL import Image

log = logging.getLogger("femtonews")
ROOT = Path(__file__).resolve().parent.parent
SGT = dt.timezone(dt.timedelta(hours=8))

GROUP_ORDER = ["Directions", "Methods", "Materials", "Applications", "Miscellaneous"]   # overridden by config `categories` order
SKIP_TITLE = re.compile(r"^(correction|erratum|corrigendum|retraction|retracted|expression of concern|withdrawn|editorial board|issue information|cover image|front cover|back cover|inside cover)\b", re.I)
ORG_WORDS = re.compile(r"(universit|institut|hospital|college|laborator|centre|center|academy|school of|polytechn|max planck|riken|cnrs|inserm|cnr\b|csic|fraunhofer|leibniz|helmholtz|national lab|clinic|faculty|ecole|école|eth\b|epfl|kaist|a\*star|nih\b|corporation|inc\.|ltd|gmbh)", re.I)
DEPT_WORDS = re.compile(r"^(dep(t|artment)|division|section|unit|group|key laborator|state key|program|graduate school|faculty of|school of medicine)", re.I)


# --------------------------------------------------------------------------- config / http

def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_include"] = [re.compile(p, re.I) for p in cfg.get("include", [])]
    cfg["_exclude"] = [re.compile(p, re.I) for p in cfg.get("exclude", [])]
    cfg["_strong"] = [re.compile(p, re.I) for p in cfg.get("strong_include", [])]
    cfg["_title_only"] = [re.compile(p, re.I) for p in cfg.get("title_only", [])]
    cfg["_charlist"] = re.compile(cfg["characterisation_list"], re.I) if cfg.get("characterisation_list") else None
    cfg["_excl_sources"] = [x.lower() for x in cfg.get("exclude_sources", [])]
    cj = cfg.get("core_journals") or {}
    cfg["_core_issn"] = {x.lower() for x in cj.get("issn", [])}
    cfg["_core_titles"] = {x.lower() for x in cj.get("titles", [])}
    cats = []
    for group, entries in (cfg.get("categories") or {}).items():
        for name, pats in entries.items():
            cats.append((group, name, [re.compile(p, re.I) for p in pats]))
    cfg["_cats"] = cats
    cfg["_groups"] = list((cfg.get("categories") or {}).keys()) or GROUP_ORDER
    cfg["_group_limits"] = cfg.get("category_limits") or {}
    cfg["_glyphs"] = [(c, g) for c, g in (cfg.get("glyphs") or [])]
    comp = lambda xs: [re.compile(p, re.I) for p in (xs or [])]
    cfg["_exclude_title"] = comp(cfg.get("exclude_title"))
    tq = cfg.get("technique") or {}
    cfg["_tech_terms"], cfg["_tech_cues"] = comp(tq.get("terms")), comp(tq.get("cues"))
    cfg["_tech_strong"] = comp(tq.get("strong_cues"))
    cfg["_tech_journals"] = comp(tq.get("journals"))
    ra = cfg.get("routine_application") or {}
    cfg["_routine_domain"], cfg["_routine_method"] = comp(ra.get("domain")), comp(ra.get("method"))
    soft = cfg.get("soft_exclude") or {}
    cfg["_soft"], cfg["_soft_unless"] = comp(soft.get("patterns")), comp(soft.get("unless_title"))
    cfg["_topics"] = [{"name": t.get("name", ""), "title": comp(t.get("title")), "abstract": comp(t.get("abstract")),
                       "exclude": comp(t.get("exclude"))} for t in (cfg.get("extra_topics") or [])]
    return cfg


class Http:
    """requests wrapper: one User-Agent, per-host delay, optional robots.txt check."""

    def __init__(self, cfg: dict):
        h = cfg.get("http", {})
        self.ua = h.get("user_agent", "LightFieldNewsBot/1.0").format(site_url=cfg.get("site", {}).get("url", ""))
        self.timeout = h.get("timeout_s", 20)
        self.delay = h.get("delay_s", 1.0)
        self.api_delay = h.get("api_delay_s", 0.2)
        self.api_hosts = set(h.get("api_hosts", ["api.openalex.org", "api.crossref.org", "www.ebi.ac.uk", "export.arxiv.org"]))
        self.last_status: int | None = None
        self.last_url: str = ""
        self.respect_robots = h.get("respect_robots_txt", True)
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": self.ua, "Accept-Language": "en"})
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def _wait(self, host: str):
        last = self._last.get(host)
        delay = self.api_delay if host in self.api_hosts else self.delay
        if last is not None:
            gap = time.time() - last
            if gap < delay:
                time.sleep(delay - gap)
        self._last[host] = time.time()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(base + "/robots.txt", timeout=self.timeout)
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
                self._robots[base] = rp
            except requests.RequestException:
                self._robots[base] = None
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(self.ua, url)

    def get(self, url: str, check_robots: bool = False, **kw) -> requests.Response | None:
        if check_robots and not self.allowed(url):
            log.info("robots.txt disallows %s", url)
            return None
        self._wait(urlparse(url).netloc)
        self.last_status, self.last_url = None, url
        try:
            r = self.s.get(url, timeout=self.timeout, allow_redirects=True, **kw)
        except requests.RequestException as e:
            log.warning("GET %s failed: %s", url, e)
            return None
        self.last_status, self.last_url = r.status_code, r.url
        if r.status_code >= 400:
            log.info("GET %s -> HTTP %s", url, r.status_code)
            return None
        return r


# --------------------------------------------------------------------------- text helpers

LATEX_SYMBOLS = {"textmu": "µ", "mu": "µ", "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ",
                 "epsilon": "ε", "kappa": "κ", "lambda": "λ", "pi": "π", "sigma": "σ", "tau": "τ", "omega": "ω",
                 "times": "×", "pm": "±", "sim": "~", "approx": "≈", "circ": "°", "AA": "Å", "%": "%"}


def delatex(s: str) -> str:
    """Strip the simple LaTeX that arXiv titles and abstracts carry (Cs$_2$SnI$_6$, {\textmu}m, $α$-MnTe)."""
    if "\\" not in s and "$" not in s:
        return s
    s = re.sub(r"\\texorpdfstring\{((?:[^{}]|\{[^{}]*\})*)\}\{[^{}]*\}", r"\1", s)
    s = re.sub(r"\\([A-Za-z]+|%)\b ?", lambda m: LATEX_SYMBOLS.get(m.group(1), m.group(0)), s)
    s = re.sub(r"\$([^$]{0,80})\$", lambda m: re.sub(r"[_^{}]|\\(mathrm|rm|text)", "", m.group(1)), s)
    s = re.sub(r"\{([^{}\\]{0,40})\}", r"\1", s)
    return s


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)          # JATS / HTML tags
    s = html.unescape(s)
    s = delatex(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^(abstract|summary)[\s:.\-–]+", "", s, flags=re.I)
    return s


def trim_summary(s: str, max_chars: int) -> str:
    s = clean_text(s)
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if end > max_chars * 0.5:
        return cut[: end + 1] + " …"
    return cut.rsplit(" ", 1)[0] + " …"


def slug_for(rec: dict) -> str:
    key = rec.get("doi") or rec.get("url") or rec["title"]
    base = re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")[:60]
    return base + "-" + hashlib.sha1(key.lower().encode()).hexdigest()[:6]


def title_key(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())[:90]


def norm_doi(d: str | None) -> str | None:
    if not d:
        return None
    d = d.strip()
    d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d, flags=re.I)
    return d.lower() or None


# --------------------------------------------------------------------------- relevance / classification

def is_core_journal(rec: dict, cfg: dict) -> bool:
    j = (rec.get("journal") or "").strip().lower()
    issns = {str(x).lower() for x in (rec.get("issns") or []) + [rec.get("issn")] if x}
    return j in cfg.get("_core_titles", set()) or bool(issns & cfg.get("_core_issn", set()))


def is_technique(rec: dict, cfg: dict, strong: bool = False) -> bool:
    """Technique / process / method development in ultrafast laser fabrication.
    Such papers must never be missed: they bypass the routine-application, title and soft exclusions,
    need only ONE mention of a technique term, and go straight to the front page."""
    title, abstract = rec.get("title", ""), rec.get("abstract", "")
    text = f"{title} {abstract}"
    if not any(p.search(text) for p in cfg.get("_tech_terms", [])):
        return False
    return any(p.search(text) for p in cfg.get("_tech_strong" if strong else "_tech_cues", []))


def is_relevant(rec: dict, cfg: dict) -> bool:
    title, abstract = rec.get("title", ""), rec.get("abstract", "")
    if SKIP_TITLE.search(title):
        return False
    if rec.get("_force"):                      # submitted through data/inbox.txt
        return True
    journal = (rec.get("journal") or "").lower()
    if rec.get("_type") in ("dataset", "paratext", "other", "erratum", "retraction") or any(x == journal or journal.startswith(x) for x in cfg.get("_excl_sources", [])):
        return False
    if is_routine_application(title, cfg) and not is_technique(rec, cfg):   # e.g. "NIR grading of tea"
        return False
    if is_core_journal(rec, cfg):              # core laser-processing journals: everything
        return True
    text = f"{title} {abstract}"
    if any(p.search(text) for p in cfg["_exclude"]):
        return False
    technique = is_technique(rec, cfg)
    if technique:                              # method papers: one mention of any technique term is enough
        return True
    if any(p.search(title) for p in cfg.get("_exclude_title", [])):
        return False
    # off-topic uses (e.g. ultrafast spectroscopy, laser physics without processing) unless the title names a fabrication term
    if any(p.search(text) for p in cfg.get("_soft", [])) and not any(p.search(title) for p in cfg.get("_soft_unless", [])):
        return False
    if any(p.search(title) for p in cfg["_include"]):
        return True
    body = cfg["_charlist"].sub(" ", abstract) if cfg.get("_charlist") else abstract
    for p in cfg.get("_title_only", []):
        body = p.sub(" ", body)
    if any(p.search(body) for p in cfg.get("_strong", [])):
        return True
    hits = sum(len(p.findall(body)) for p in cfg["_include"])
    if hits >= 2:
        return True
    if hits >= 1 and any(p.search(rec.get("journal") or "") for p in cfg.get("_tech_journals", [])):
        return True                            # optics / instrumentation journals: one mention is enough
    return any(topic_match(rec, t) for t in cfg.get("_topics", []))


def is_routine_application(title: str, cfg: dict) -> bool:
    d, m = cfg.get("_routine_domain"), cfg.get("_routine_method")
    return bool(d and m and any(p.search(title) for p in d) and any(p.search(title) for p in m))


def topic_match(rec: dict, topic: dict) -> bool:
    """Extra topics (laser-written photonics, light-field engineering for processing): a title match,
    or one abstract mention of a specific term, unless a topic-specific exclusion applies."""
    title, abstract = rec.get("title", ""), rec.get("abstract", "")
    if not (any(p.search(title) for p in topic["title"]) or any(p.search(abstract) for p in topic["abstract"])):
        return False
    return not any(p.search(f"{title} {abstract}") for p in topic["exclude"])


def categorize(rec: dict, cfg: dict, limit: int = 5) -> list[str]:
    """Tags from the config `categories` rules, group by group, each group capped by `category_limits`."""
    text = f"{rec.get('title', '')} {rec.get('abstract', '')}"
    groups = cfg.get("_groups") or GROUP_ORDER
    found: dict[str, list[str]] = {g: [] for g in groups}
    for group, name, pats in cfg["_cats"]:
        if any(p.search(text) for p in pats):
            found.setdefault(group, []).append(name)
    misc = found.setdefault("Miscellaneous", [])
    if rec.get("is_review") and "Reviews" not in misc:
        misc.append("Reviews")
    caps = cfg.get("_group_limits") or {}
    ordered: list[str] = []
    for g in groups:
        ordered += found.get(g, [])[: caps.get(g, 2)]
    if not ordered:
        ordered = [cfg.get("default_category", "General Processing")]
    return ordered[:limit]


def glyph_for(cats: list[str], cfg: dict | None = None) -> str:
    """Fallback thumbnail drawn by the site: the first config `glyphs` entry whose category is tagged."""
    for cat, glyph in (cfg or {}).get("_glyphs", []):
        if cat in cats:
            return glyph
    return (cfg or {}).get("default_glyph", "pulse")


def institutions_from(affiliations: list[str], limit: int = 3) -> list[str]:
    """Pick organisation names out of free-text affiliation strings."""
    out: list[str] = []
    for aff in affiliations:
        aff = re.sub(r"\S+@\S+", "", aff)                      # e-mails
        aff = re.sub(r"^\s*[\d*†‡§#]+\s*", "", aff)            # footnote markers
        aff = re.sub(r"\s+\d+\s*$", "", aff)                    # trailing affiliation numbers
        parts = [p.strip(" .;") for p in re.split(r",|;", aff) if p.strip(" .;")]
        org = None
        for p in parts:
            if ORG_WORDS.search(p) and not DEPT_WORDS.search(p):
                org = p
                break
        if not org:
            for p in parts:                                     # fall back to a department-level match
                if ORG_WORDS.search(p):
                    org = p
                    break
        if org and len(org) <= 90:
            org = re.sub(r"\s+", " ", org)
            if org.lower() not in (o.lower() for o in out):
                out.append(org)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- sources

def europepmc_search(http: Http, cfg: dict, since: str, until: str, date_field: str = "FIRST_PDATE") -> list[dict]:
    """date_field: FIRST_PDATE (publication) or CREATION_DATE (entered Europe PMC; catches late-indexed papers)."""
    terms = " OR ".join([f"TITLE_ABS:{t}" for t in cfg.get("europepmc_terms", ["femtosecond"])] + [f"KW:{t}" for t in cfg.get("europepmc_terms", ["femtosecond"])])
    query = f"({terms}) AND {date_field}:[{since} TO {until}]"
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    cursor, out, cap = "*", [], cfg["sources"].get("max_europepmc", 6000)
    while len(out) < cap:
        r = http.get(url, params={"query": query, "format": "json", "resultType": "core", "pageSize": 200, "cursorMark": cursor})
        if not r:
            break
        data = r.json()
        for res in data.get("resultList", {}).get("result", []):
            out.append(parse_europepmc(res))
        nxt = data.get("nextCursorMark")
        if not nxt or nxt == cursor or not data.get("resultList", {}).get("result"):
            break
        cursor = nxt
    log.info("Europe PMC: %d records", len(out))
    return out


def parse_europepmc(res: dict) -> dict:
    authors, affs = [], []
    for a in (res.get("authorList") or {}).get("author", []):
        name = a.get("fullName") or " ".join(x for x in [a.get("firstName"), a.get("lastName")] if x)
        if name:
            authors.append(name)
        for d in (a.get("authorAffiliationDetailsList") or {}).get("authorAffiliation", []):
            if d.get("affiliation"):
                affs.append(d["affiliation"])
        if a.get("affiliation"):
            affs.append(a["affiliation"])
    ji = (res.get("journalInfo") or {}).get("journal") or {}
    journal = ji.get("title") or ji.get("medlineAbbreviation") or ""
    if res.get("source") == "PPR":
        journal = (res.get("bookOrReportDetails") or {}).get("publisher") or "Preprint"
    pubtypes = [p.lower() for p in (res.get("pubTypeList") or {}).get("pubType", [])]
    lic = (res.get("license") or "").lower()
    doi = norm_doi(res.get("doi"))
    return {
        "source": "europepmc",
        "title": clean_text(res.get("title")).rstrip("."),
        "authors": authors or [s.strip() for s in (res.get("authorString") or "").rstrip(".").split(",") if s.strip()],
        "affiliations": affs,
        "journal": journal,
        "issn": ji.get("issn") or ji.get("essn"),
        "doi": doi,
        "pmid": res.get("pmid"),
        "pmcid": res.get("pmcid"),
        "url": f"https://doi.org/{doi}" if doi else (f"https://europepmc.org/article/{res.get('source')}/{res.get('id')}"),
        "date": res.get("firstPublicationDate") or res.get("firstIndexDate") or "",
        "abstract": clean_text(res.get("abstractText")),
        "oa": res.get("isOpenAccess") == "Y" or lic.startswith("cc"),
        "license": lic.upper().replace("CC ", "CC ") if lic else "",
        "is_review": any("review" in p for p in pubtypes),
        "is_preprint": res.get("source") == "PPR",
    }


def openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    return " ".join(w for _, w in sorted(pos))


def parse_openalex(w: dict) -> dict:
    authors, insts = [], []
    for a in w.get("authorships") or []:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
        for inst in a.get("institutions") or []:
            n = inst.get("display_name")
            if n and n not in insts:
                insts.append(n)
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    oa = w.get("open_access") or {}
    lic = ((w.get("best_oa_location") or {}).get("license") or loc.get("license") or "").lower()
    is_oa = oa.get("oa_status") in ("gold", "diamond", "hybrid") and lic.startswith("cc")
    m = re.match(r"cc-([a-z\-]+)", lic)
    doi = norm_doi(w.get("doi"))
    return {
        "source": "openalex",
        "title": clean_text(w.get("display_name") or w.get("title")).rstrip("."),
        "authors": authors,
        "affiliations": [],
        "institutions_direct": insts,
        "journal": src.get("display_name") or "",
        "issn": src.get("issn_l"),
        "issns": src.get("issn") or [],
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else (loc.get("landing_page_url") or ""),
        "date": w.get("publication_date") or "",
        "abstract": clean_text(openalex_abstract(w.get("abstract_inverted_index"))),
        "oa": bool(is_oa),
        "license": ("CC " + m.group(1).upper()) if (is_oa and m) else "",
        "is_review": w.get("type") == "review",
        "is_preprint": w.get("type") == "preprint" or src.get("type") == "repository",
        "_openalex_source": (src.get("id") or "").rsplit("/", 1)[-1],
        "_type": w.get("type"),
    }


def openalex_search(http: Http, cfg: dict, since: str, until: str) -> list[dict]:
    out = []
    cap = cfg["sources"].get("max_openalex_per_term", 6000)
    select = "id,doi,display_name,publication_date,authorships,primary_location,open_access,best_oa_location,abstract_inverted_index,type"
    for term in cfg.get("openalex_terms", []):
        cursor, n = "*", 0
        while cursor and n < cap:
            r = http.get("https://api.openalex.org/works", params={
                "filter": f"title_and_abstract.search:{term},from_publication_date:{since},to_publication_date:{until},type:article|review|preprint|letter",
                "per-page": 200, "cursor": cursor, "select": select})
            if not r:
                break
            data = r.json()
            results = data.get("results") or []
            out += [parse_openalex(w) for w in results]
            n += len(results)
            cursor = (data.get("meta") or {}).get("next_cursor")
            if not results:
                break
    log.info("OpenAlex: %d records", len(out))
    return out


def crossref_core_journals(http: Http, cfg: dict, since: str, until: str) -> list[dict]:
    """Every article that core journals registered in the window (no keyword filter)."""
    out = []
    for issn in (cfg.get("core_journals") or {}).get("issn", []):
        r = http.get("https://api.crossref.org/works", params={
            "filter": f"issn:{issn},from-created-date:{since},until-created-date:{until},type:journal-article",
            "rows": 1000,
            "select": "DOI,title,author,container-title,ISSN,created,issued,published-online,published-print,abstract,license,type,subject"})
        if r:
            out += [parse_crossref(it) for it in r.json().get("message", {}).get("items", [])]
    log.info("Crossref core journals: %d records", len(out))
    return out


def crossref_search(http: Http, cfg: dict, since: str, until: str) -> list[dict]:
    out = []
    rows = min(cfg["sources"].get("max_crossref_rows", 1000), 1000)
    for q in cfg.get("crossref_queries", []):
        r = http.get("https://api.crossref.org/works", params={
            "query.bibliographic": q,
            "filter": f"from-created-date:{since},until-created-date:{until},type:journal-article",
            "rows": rows,
            "select": "DOI,title,author,container-title,ISSN,created,issued,published-online,published-print,abstract,license,type,subject",
        })
        if not r:
            continue
        for it in r.json().get("message", {}).get("items", []):
            out.append(parse_crossref(it))
    log.info("Crossref: %d records", len(out))
    return out


def _cr_date(it: dict) -> str:
    for k in ("published-online", "issued", "published-print", "created"):
        parts = (it.get(k) or {}).get("date-parts") or [[]]
        p = parts[0]
        if p and p[0]:
            y, m, d = (p + [1, 1])[:3]
            return f"{y:04d}-{m:02d}-{d:02d}"
    return ""


def parse_crossref(it: dict) -> dict:
    authors, affs = [], []
    for a in it.get("author", []) or []:
        name = " ".join(x for x in [a.get("given"), a.get("family")] if x) or a.get("name")
        if name:
            authors.append(name)
        for af in a.get("affiliation", []) or []:
            if af.get("name"):
                affs.append(af["name"])
    lic_urls = [l.get("URL", "") for l in it.get("license", []) or []]
    cc = next((u for u in lic_urls if "creativecommons.org" in u), "")
    lic = ""
    if cc:
        m = re.search(r"licenses/([a-z\-]+)/([\d.]+)", cc)
        lic = f"CC {m.group(1).upper()} {m.group(2)}" if m else "CC"
    doi = norm_doi(it.get("DOI"))
    return {
        "source": "crossref",
        "title": clean_text((it.get("title") or [""])[0]).rstrip("."),
        "authors": authors,
        "affiliations": affs,
        "journal": (it.get("container-title") or [""])[0],
        "issn": (it.get("ISSN") or [None])[0],
        "issns": it.get("ISSN") or [],
        "doi": doi,
        "url": f"https://doi.org/{doi}" if doi else "",
        "date": _cr_date(it),
        "abstract": clean_text(it.get("abstract")),
        "oa": bool(cc),
        "license": lic,
        "is_review": False,
        "is_preprint": False,
    }


def arxiv_search(http: Http, cfg: dict, since: str, until: str) -> list[dict]:
    cats = " OR ".join(f"cat:{c}" for c in cfg.get("arxiv_categories", []))
    terms = " OR ".join(f"abs:{t}" for t in cfg.get("arxiv_terms", ["femtosecond"]))
    q = f"({cats}) AND ({terms})"
    out, page, limit = [], 200, cfg["sources"].get("max_arxiv", 2000)
    for start in range(0, limit, page):        # newest first; stop once a page reaches before `since`
        r = http.get("http://export.arxiv.org/api/query", params={"search_query": q, "sortBy": "submittedDate", "sortOrder": "descending", "start": start, "max_results": page})
        if not r:
            break
        recs = parse_arxiv(r.text, since, until)
        out += recs
        dates = re.findall(r"<published>(\d{4}-\d{2}-\d{2})", r.text)
        if not dates or min(dates) < since or len(dates) < page:
            break
    return out


def parse_arxiv(xml_text: str, since: str, until: str) -> list[dict]:
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    out = []
    root = ET.fromstring(xml_text)
    for e in root.findall("a:entry", ns):
        date = (e.findtext("a:published", default="", namespaces=ns) or "")[:10]
        if not (since <= date <= until):
            continue
        abs_url = e.findtext("a:id", default="", namespaces=ns).strip()
        arxiv_id = re.sub(r"^.*/abs/", "", abs_url)
        arxiv_id_nov = re.sub(r"v\d+$", "", arxiv_id)
        authors, affs = [], []
        for a in e.findall("a:author", ns):
            authors.append(a.findtext("a:name", default="", namespaces=ns).strip())
            aff = a.findtext("arxiv:affiliation", default="", namespaces=ns)
            if aff:
                affs.append(aff)
        out.append({
            "source": "arxiv",
            "title": clean_text(e.findtext("a:title", default="", namespaces=ns)),
            "authors": authors,
            "affiliations": affs,
            "journal": "arXiv (preprint)",
            "issn": None,
            "doi": f"10.48550/arxiv.{arxiv_id_nov}".lower(),
            "url": f"https://arxiv.org/abs/{arxiv_id_nov}",
            "date": date,
            "abstract": clean_text(e.findtext("a:summary", default="", namespaces=ns)),
            "oa": True,
            "license": "",
            "is_review": False,
            "is_preprint": True,
        })
    log.info("arXiv: %d records in window", len(out))
    return out


def crossref_find_doi(http: Http, title: str) -> str | None:
    """DOI for an exact title match on Crossref (used when an article was added without a DOI)."""
    r = http.get("https://api.crossref.org/works", params={"query.bibliographic": title, "rows": 5, "select": "DOI,title"})
    if not r:
        return None
    want = title_key(title)
    for it in r.json().get("message", {}).get("items", []):
        if title_key((it.get("title") or [""])[0]) == want:
            return norm_doi(it.get("DOI"))
    return None


def lookup_doi(http: Http, doi: str) -> dict | None:
    """Fetch one paper by DOI (for data/inbox.txt submissions): Europe PMC first, Crossref second."""
    r = http.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"})
    if r:
        res = r.json().get("resultList", {}).get("result", [])
        if res:
            return parse_europepmc(res[0])
    m = re.match(r"10\.48550/arxiv\.(.+)$", doi, re.I)
    if m:                                       # arXiv DOIs are registered with DataCite, not Crossref
        r = http.get("http://export.arxiv.org/api/query", params={"id_list": m.group(1), "max_results": 1})
        recs = parse_arxiv(r.text, "0000-00-00", "9999-99-99") if r else []
        return recs[0] if recs else None
    r = http.get(f"https://api.crossref.org/works/{doi}")
    if r:
        return parse_crossref(r.json().get("message", {}))
    return None


# --------------------------------------------------------------------------- merge

class VenueChecker:
    """Keep papers from established publishers and the main preprint servers; drop predatory or
    low-visibility venues. Unknown publishers pass if their OpenAlex h-index is high enough."""

    def __init__(self, http, cfg: dict, root: Path, online: bool = True):
        v = cfg.get("venues") or {}
        self.http, self.cfg, self.online = http, cfg, online
        self.trusted = {p.lower() for p in v.get("trusted_doi_prefixes", [])}
        self.blocked = {p.lower() for p in v.get("blocked_doi_prefixes", [])}
        self.blocked_journals = [re.compile(p, re.I) for p in v.get("blocked_journals", [])]
        self.trusted_journals = [re.compile(p, re.I) for p in v.get("trusted_journals", [])]
        self.min_h = int(v.get("min_h_index", 40))
        self.path = root / "data" / "venues.json"
        self.cache: dict = load_json(self.path, {})
        self.enabled = bool(v)

    def h_index(self, rec: dict) -> int:
        keys = [rec.get("_openalex_source")] + [f"issn:{x}" for x in (rec.get("issns") or []) + [rec.get("issn")] if x]
        keys = [k for k in keys if k]
        for k in keys:
            if k in self.cache:
                return self.cache[k]
        if not self.online:
            return 0
        for k in keys:
            r = self.http.get(f"https://api.openalex.org/sources/{k}")
            if r is not None:
                try:
                    h = int((r.json().get("summary_stats") or {}).get("h_index") or 0)
                except ValueError:
                    h = 0
                self.cache[k] = h
                return h
        if keys:
            self.cache[keys[0]] = 0
        return 0

    def ok(self, rec: dict) -> bool:
        if not self.enabled or rec.get("_force") or is_core_journal(rec, self.cfg):
            return True
        if any(p.search(rec.get("journal") or "") for p in self.blocked_journals):
            return False
        if any(p.search(rec.get("journal") or "") for p in self.trusted_journals):
            return True
        prefix = (norm_doi(rec.get("doi") or "") or "").split("/")[0].lower()
        if prefix in self.blocked:
            return False
        if prefix in self.trusted or is_technique(rec, self.cfg):    # method papers are kept from any unblocked venue
            return True
        return self.h_index(rec) >= self.min_h

    def save(self):
        self.path.write_text(json.dumps(self.cache, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def merge_records(records: list[dict]) -> list[dict]:
    """Deduplicate by DOI, then by normalised title. Europe PMC wins; Crossref fills gaps."""
    rank = {"europepmc": 0, "openalex": 1, "crossref": 2, "arxiv": 3, "inbox": -1}
    records = sorted(records, key=lambda r: rank.get(r["source"], 9))
    by_doi: dict[str, dict] = {}
    by_title: dict[str, dict] = {}
    out: list[dict] = []
    for r in records:
        if not r.get("title"):
            continue
        k_doi, k_t = r.get("doi"), title_key(r["title"])
        hit = (by_doi.get(k_doi) if k_doi else None) or by_title.get(k_t)
        if hit:
            for f in ("abstract", "affiliations", "institutions_direct", "journal", "issn", "license", "date", "_openalex_source"):
                if not hit.get(f) and r.get(f):
                    hit[f] = r[f]
            hit["issns"] = list(dict.fromkeys((hit.get("issns") or []) + (r.get("issns") or [])))
            hit["oa"] = hit.get("oa") or r.get("oa")
            hit["_force"] = hit.get("_force") or r.get("_force")
            continue
        out.append(r)
        if k_doi:
            by_doi[k_doi] = r
        by_title[k_t] = r
    return out


# --------------------------------------------------------------------------- figures

def extract_preview_image(page_html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(page_html, "html.parser")
    candidates = []
    for attrs in ({"property": "og:image"}, {"property": "og:image:url"}, {"name": "twitter:image"}, {"name": "twitter:image:src"}, {"property": "twitter:image"}):
        for m in soup.find_all("meta", attrs=attrs):
            if m.get("content"):
                candidates.append(m["content"].strip())
    link = soup.find("link", rel=lambda v: v and "image_src" in v)
    if link and link.get("href"):
        candidates.append(link["href"].strip())
    for c in candidates:
        if c and not c.startswith("data:"):
            return urljoin(base_url, c)
    return None


def upgrade_image_url(url: str) -> str:
    """Ask image CDNs for a larger rendition (e.g. Springer Nature serves 92-px covers in og:image)."""
    if "media.springernature.com" in url:
        url = re.sub(r"/(w|lw|m|full)\d*/", "/w316/", url, count=1)
    return url


def _img_src(tag, base_url: str) -> str | None:
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        v = tag.get(attr)
        if v and not v.startswith("data:"):
            return urljoin(base_url, v.strip())
    return None


def _foreign_doi(src: str, doi: str | None) -> bool:
    """True if an image URL embeds a DOI that is not this article's (e.g. 'related articles' thumbnails)."""
    from urllib.parse import unquote
    dec = unquote(unquote(src)).lower()
    found = re.findall(r"10\.\d{4,9}/[^/?#\s]+", dec)
    if not found:
        return False
    if not doi:
        return True
    suffix = doi.lower().split("/", 1)[-1]
    return not any(suffix in f for f in found)


def extract_first_figure(page_html: str, base_url: str, doi: str | None = None) -> str | None:
    """First figure of THIS article on a page (for open-access pages that publish no og:image)."""
    soup = BeautifulSoup(page_html, "html.parser")
    selectors = ["figure img", "img.figure_img", "div.figure img", "div[class*=fig] img", "img[class*=fig]", "img[src*=fig]", "img[data-src*=fig]"]
    for sel in selectors:
        for tag in soup.select(sel):
            src = _img_src(tag, base_url)
            if src and not re.search(r"logo|icon|sprite|orcid|badge|cover", src, re.I) and not _foreign_doi(src, doi):
                return src
    return None


def extract_cover_candidates(page_html: str, base_url: str) -> list[str]:
    """Possible journal-cover images on a journal homepage: og:image first, then <img> marked as a cover."""
    out = []
    og = extract_preview_image(page_html, base_url)
    if og:
        out.append(og)
    soup = BeautifulSoup(page_html, "html.parser")
    for tag in soup.find_all("img"):
        hay = " ".join([tag.get("src") or "", tag.get("data-src") or "", " ".join(tag.get("class") or []), tag.get("alt") or ""])
        if re.search(r"cover", hay, re.I):
            src = _img_src(tag, base_url)
            if src:
                out.append(src)
    seen, uniq = set(), []
    for u in (upgrade_image_url(u) for u in out):
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def looks_like_cover(url: str) -> bool:
    return bool(re.search(r"cover|journal[-_]?image|jcover|issue[-_]?image", url, re.I))


def load_image(img_bytes: bytes, cfg: dict) -> Image.Image | None:
    """Decode an image; return None if it is not an image or is logo-sized."""
    try:
        im = Image.open(io.BytesIO(img_bytes))
        im.load()
    except Exception as e:  # noqa: BLE001 — any decode failure means "no usable image"
        log.info("not an image: %s", e)
        return None
    if min(im.size) < cfg.get("figures", {}).get("min_side_px", 120):
        return None
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        rgba = im.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    return im.convert("RGB")


def is_portrait(im: Image.Image) -> bool:
    """Journal covers are portrait (roughly 3:4); figures and graphical abstracts mostly are not."""
    w, h = im.size
    return h >= 1.15 * w


def write_thumbnail(im: Image.Image, dest: Path, cfg: dict) -> None:
    fc = cfg.get("figures", {})
    im = im.copy()
    im.thumbnail((fc.get("max_width", 400), fc.get("max_height", 280)), Image.LANCZOS)
    dest.parent.mkdir(parents=True, exist_ok=True)
    im.save(dest, "JPEG", quality=82, optimize=True, progressive=True)


def save_thumbnail(img_bytes: bytes, dest: Path, cfg: dict) -> bool:
    im = load_image(img_bytes, cfg)
    if im is None:
        return False
    write_thumbnail(im, dest, cfg)
    return True


def openalex_work(http: Http, doi: str) -> dict | None:
    """Open-access status, licence and journal (source) of a DOI from OpenAlex."""
    r = http.get(f"https://api.openalex.org/works/doi:{doi}")
    if not r:
        return None
    w = r.json()
    oa = w.get("open_access") or {}
    best = w.get("best_oa_location") or {}
    prim_loc = w.get("primary_location") or {}
    prim = prim_loc.get("source") or {}
    return {
        "is_oa": bool(oa.get("is_oa")),
        "oa_status": oa.get("oa_status") or "",
        "license": best.get("license") or prim_loc.get("license") or "",
        "source_id": (prim.get("id") or "").rsplit("/", 1)[-1],
        "issn": prim.get("issn_l"),
    }


def enrich_oa(http: Http, rec: dict) -> dict:
    """Mark a paper open access when OpenAlex reports a gold/diamond/hybrid CC-licensed version."""
    doi = rec.get("doi") or ""
    if not doi or doi.startswith("10.48550/"):
        return rec
    w = openalex_work(http, doi)
    if not w:
        return rec
    rec["_openalex_source"] = w["source_id"]
    rec["issn"] = rec.get("issn") or w["issn"]
    lic = (w["license"] or "").lower()
    if w["oa_status"] in ("gold", "diamond", "hybrid") and lic.startswith("cc"):
        rec["oa"] = True
        m = re.match(r"cc-([a-z\-]+)", lic)
        if not rec.get("license") or rec.get("license") == "Open access":
            rec["license"] = "CC " + m.group(1).upper() if m else "CC"
    return rec


class FigureFetcher:
    """Thumbnail policy (config figures.mode):

    oa_figure_else_cover (default)  open-access paper -> its own preview image (Fig. 1 / graphical abstract);
                                    paywalled paper   -> journal cover only.
    all                             every paper -> its preview image, journal cover as fallback.
    Nothing found -> None, and the site draws a technique glyph.
    """

    def __init__(self, http: Http, cfg: dict, root: Path):
        self.http, self.cfg, self.root = http, cfg, root
        self.cover_cache_path = root / "covers" / "index.json"
        try:
            self.cover_cache = json.loads(self.cover_cache_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.cover_cache = {}
        self.manual_covers = {k.lower(): v for k, v in (self.cfg.get("journal_covers") or {}).items()}
        self._pages: dict[str, tuple[str | None, str, str, str]] = {}
        self._fails: dict[str, int] = {}          # DOI prefix / host -> number of 401/403/429 answers
        self._no_cover: set[str] = set()          # journals with no findable cover this run

    BLOCK_AFTER = 2

    @staticmethod
    def _doi_prefix(url: str) -> str | None:
        m = re.match(r"https?://(dx\.)?doi\.org/(10\.\d{4,9})/", url or "", re.I)
        return m.group(2) if m else None

    def _blocked(self, url: str) -> bool:
        keys = [k for k in (self._doi_prefix(url), urlparse(url).netloc) if k]
        return any(self._fails.get(k, 0) >= self.BLOCK_AFTER for k in keys)

    def _note_failure(self, url: str):
        if getattr(self.http, "last_status", None) in (401, 403, 429):
            for k in {self._doi_prefix(url), urlparse(url).netloc, urlparse(getattr(self.http, "last_url", "") or "").netloc} - {None, ""}:
                self._fails[k] = self._fails.get(k, 0) + 1

    # -- helpers
    def _get_image(self, url: str | None) -> Image.Image | None:
        if not url:
            return None
        pats = self.cfg.get("figures", {}).get("reject_url_patterns", [])
        if any(p in url.lower() for p in pats) or self._blocked(url):
            return None
        r = self.http.get(url, check_robots=True)
        if not r:
            self._note_failure(url)
            return None
        if not r.headers.get("Content-Type", "image").startswith("image"):
            return None
        return load_image(r.content, self.cfg)

    def _page(self, url: str | None) -> tuple[str | None, str, str, str]:
        """(preview image URL, host, html, final URL) of a page, fetched once per run."""
        if not url:
            return None, "", "", ""
        if url not in self._pages:
            if self._blocked(url):
                return None, urlparse(url).netloc.replace("www.", ""), "", url
            page = self.http.get(url, check_robots=True)
            if page is None:
                self._note_failure(url)
            if page is not None and "html" in page.headers.get("Content-Type", "html"):
                self._pages[url] = (extract_preview_image(page.text, page.url), urlparse(page.url).netloc.replace("www.", ""), page.text, page.url)
            else:
                self._pages[url] = (None, urlparse(url).netloc.replace("www.", ""), "", url)
        return self._pages[url]

    def _preview(self, url: str | None) -> tuple[str | None, str]:
        img, host, _, _ = self._page(url)
        return img, host

    def journal_key(self, rec: dict) -> str:
        return (rec.get("journal") or rec.get("issn") or "").strip().lower()

    def _store_cover(self, rec: dict, im: Image.Image) -> dict:
        key = self.journal_key(rec)
        name = "covers/" + (re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:60] or "journal") + ".jpg"
        write_thumbnail(im, self.root / name, self.cfg)
        entry = {"src": name, "kind": "cover", "credit": f"Journal cover · {rec.get('journal', '')}"}
        if key:
            self.cover_cache[key] = entry
        return dict(entry)

    def _homepage_cover(self, rec: dict) -> Image.Image | None:
        """Journal homepage (found via OpenAlex) -> an image there that looks like the cover."""
        src = rec.get("_openalex_source")
        if not src and rec.get("issn"):
            src = f"issn:{rec['issn']}"
        if not src:
            return None
        r = self.http.get(f"https://api.openalex.org/sources/{src}")
        home = r.json().get("homepage_url") if r else None
        _, _, html_text, final = self._page(home)
        if not html_text:
            return None
        for cand in extract_cover_candidates(html_text, final)[:4]:
            im = self._get_image(cand)
            if im is not None and (is_portrait(im) or looks_like_cover(cand)):
                return im
        return None

    # -- public
    def cover_for(self, rec: dict, landing_img_url: str | None = None) -> dict | None:
        key = self.journal_key(rec)
        if key and key in self.cover_cache and (self.root / self.cover_cache[key]["src"]).exists():
            return dict(self.cover_cache[key])
        if key and key in self._no_cover and not landing_img_url:
            return None
        manual = self.manual_covers.get(key) or (self.manual_covers.get((rec.get("issn") or "").lower()) if rec.get("issn") else None)
        im = self._get_image(manual)
        if im is None and landing_img_url:
            cand = self._get_image(landing_img_url)
            if cand is not None and (looks_like_cover(landing_img_url) or is_portrait(cand)):
                im = cand
        if im is None and key not in self._no_cover:
            im = self._homepage_cover(rec)
        if im is None:
            if key:
                self._no_cover.add(key)
            return None
        return self._store_cover(rec, im)

    def fetch(self, rec: dict, art_id: str) -> dict | None:
        if rec.get("source") == "arxiv" or (rec.get("journal") or "").lower().startswith("arxiv"):
            return None
        mode = self.cfg.get("figures", {}).get("mode", "oa_figure_else_cover")
        want_figure = mode == "all" or bool(rec.get("oa"))
        img_url, host, html_text, final = self._page(rec.get("url"))
        if want_figure:
            fig_url = img_url if (img_url and not looks_like_cover(img_url)) else None
            im = self._get_image(fig_url)
            if im is None and html_text:
                fig_url = extract_first_figure(html_text, final, rec.get("doi"))
                im = self._get_image(fig_url)
            if im is not None:
                write_thumbnail(im, self.root / f"figs/{art_id}.jpg", self.cfg)
                lic = rec.get("license") or ("Open access" if rec.get("oa") else "© the publisher")
                return {"src": f"figs/{art_id}.jpg", "kind": "figure", "credit": f"{lic} · via {host}"}
        return self.cover_for(rec, upgrade_image_url(img_url) if img_url else None)

    def save_cache(self):
        self.cover_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cover_cache_path.write_text(json.dumps(self.cover_cache, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- issue assembly

def to_article(rec: dict, cfg: dict) -> dict:
    cats = categorize(rec, cfg)
    art_id = slug_for(rec)
    return {
        "id": art_id,
        "date": rec.get("date", "")[:10],
        "title": rec["title"],
        "authors": ", ".join(rec.get("authors", [])[:20]) + (" et al." if len(rec.get("authors", [])) > 20 else ""),
        "institutions": (rec.get("institutions_direct") or [])[:3] or institutions_from(rec.get("affiliations", [])),
        "journal": rec.get("journal", ""),
        "doi": rec.get("doi"),
        "url": rec.get("url") or (f"https://doi.org/{rec['doi']}" if rec.get("doi") else ""),
        "oa": bool(rec.get("oa")),
        "license": rec.get("license", ""),
        "categories": cats,
        "glyph": glyph_for(cats, cfg),
        "summary": trim_summary(rec.get("abstract", ""), cfg.get("summary_max_chars", 700)),
        "why": "",
        **({"tier": "notable"} if is_technique(rec, cfg, strong=True) else {}),
        "pick": False,
        "feature": False,
        "fig": None,
        "source": rec.get("source"),
    }


def default_issue_date(today: dt.date | None = None) -> dt.date:
    today = today or dt.datetime.now(SGT).date()
    return today - dt.timedelta(days=today.weekday())   # this week's Monday


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def update_index(root: Path, issue: dict):
    idx_path = root / "data" / "issues" / "index.json"
    idx = load_json(idx_path, {"issues": []})
    entry = {"id": issue["id"], "number": issue["number"], "date": issue["date"], "window": issue["window"], "count": len(issue["articles"])}
    if issue.get("updated"):
        entry["updated"] = issue["updated"]
    idx["issues"] = [i for i in idx["issues"] if i["id"] != issue["id"]] + [entry]
    idx["issues"].sort(key=lambda i: i["id"], reverse=True)
    idx_path.write_text(json.dumps(idx, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_inbox(root: Path) -> list[str]:
    p = root / "data" / "inbox.txt"
    if not p.exists():
        return []
    dois = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            d = norm_doi(line)
            if d:
                dois.append(d)
    return dois


def clear_inbox(root: Path, done: list[str]):
    p = root / "data" / "inbox.txt"
    if not p.exists() or not done:
        return
    keep = []
    for line in p.read_text(encoding="utf-8").splitlines():
        body = line.split("#", 1)[0].strip()
        if body and norm_doi(body) in done:
            continue
        keep.append(line)
    p.write_text("\n".join(keep).rstrip() + "\n", encoding="utf-8")


def refresh_figures(args, cfg: dict, root: Path, issue_date: dt.date) -> int:
    """Fetch thumbnails (and OA status) for an existing issue without crawling for new papers."""
    issue_path = root / "data" / "issues" / f"{issue_date.isoformat()}.json"
    issue = load_json(issue_path, None)
    if not issue:
        log.error("No issue file %s", issue_path)
        return 1
    http = Http(cfg)
    figs = FigureFetcher(http, cfg, root)
    done = 0
    for art in issue["articles"]:
        if art.get("fig") and not args.force:
            continue
        if not art.get("doi"):
            found = crossref_find_doi(http, art["title"])
            if found:
                art["doi"], art["url"] = found, f"https://doi.org/{found}"
                log.info("DOI found by title: %s", found)
        rec = {"doi": art.get("doi"), "url": art.get("url"), "journal": art.get("journal", ""), "oa": art.get("oa", False),
               "license": art.get("license", ""), "source": art.get("source"), "issn": art.get("issn")}
        if not args.no_openalex:
            enrich_oa(http, rec)
            art["oa"], art["license"] = bool(rec.get("oa")), rec.get("license", "")
        art["fig"] = figs.fetch(rec, art["id"])
        done += 1
        log.info("%s -> %s", art["title"][:60], (art["fig"] or {}).get("kind", "glyph"))
    if args.dry_run:
        json.dump(issue, sys.stdout, indent=2, ensure_ascii=False)
        return 0
    issue_path.write_text(json.dumps(issue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    figs.save_cache()
    print(f"ISSUE_ID={issue['id']}")
    print(f"ISSUE_NUMBER={issue['number']}")
    print(f"ADDED=0 (thumbnails refreshed for {done} articles)")
    return 0


def run(args) -> int:
    cfg = load_config(Path(args.config))
    root = Path(args.root)
    issue_date = dt.date.fromisoformat(args.issue_date) if args.issue_date else default_issue_date()
    if args.refresh_figures:
        return refresh_figures(args, cfg, root, issue_date)
    if args.daily:
        # daily mode: papers from the last few days go into this week's issue (Monday, Singapore time)
        today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(SGT).date()
        issue_date = dt.date.fromisoformat(args.issue_date) if args.issue_date else default_issue_date(today)
        until = today
        since = today - dt.timedelta(days=cfg.get("daily_lookback_days", 3))
    else:
        until = dt.date.fromisoformat(args.until) if args.until else issue_date - dt.timedelta(days=1)
        since = dt.date.fromisoformat(args.since) if args.since else until - dt.timedelta(days=6)
    s, u = since.isoformat(), until.isoformat()
    log.info("Issue %s · window %s → %s", issue_date, s, u)

    http = Http(cfg)
    records: list[dict] = []
    stats = {"date": dt.datetime.now(SGT).strftime("%Y-%m-%d %H:%M"), "issue": issue_date.isoformat(), "window": [s, u], "sources": {}}

    def add(name, recs):
        stats["sources"][name] = len(recs)
        records.extend(recs)

    if cfg["sources"].get("europepmc"):
        add("europepmc", europepmc_search(http, cfg, s, u, "CREATION_DATE" if args.daily else "FIRST_PDATE"))
    if cfg["sources"].get("openalex"):
        oa_since = (until - dt.timedelta(days=cfg.get("openalex_lookback_days", 30))).isoformat() if args.daily else s
        add("openalex", openalex_search(http, cfg, oa_since, u))
    if cfg["sources"].get("crossref"):
        add("crossref", crossref_search(http, cfg, s, u))
        add("crossref_core_journals", crossref_core_journals(http, cfg, s, u))
    if cfg["sources"].get("arxiv"):
        add("arxiv", arxiv_search(http, cfg, s, u))

    inbox = read_inbox(root)
    done_inbox = []
    for d in inbox:
        rec = lookup_doi(http, d)
        if rec:
            rec["source"], rec["_force"] = "inbox", True
            records.append(rec)
            done_inbox.append(d)

    # merge first (so a record without abstract borrows one from another source), then filter
    pool = merge_records(records)
    oldest = (until - dt.timedelta(days=cfg.get("max_age_days", 180))).isoformat()
    merged = [r for r in pool if is_relevant(r, cfg) and (r.get("_force") or not r.get("date") or r["date"][:10] >= oldest)]
    stats.update({"raw": len(records), "unique": len(pool), "relevant": len(merged)})
    log.info("%d relevant of %d unique papers (%d raw records)", len(merged), len(pool), len(records))

    issue_path = root / "data" / "issues" / f"{issue_date.isoformat()}.json"
    idx = load_json(root / "data" / "issues" / "index.json", {"issues": []})
    existing = load_json(issue_path, None)
    number = existing["number"] if existing else (max([i["number"] for i in idx["issues"]], default=0) + 1)
    first_day = issue_date.isoformat() if args.daily else s
    issue = existing or {"id": issue_date.isoformat(), "number": number, "date": issue_date.isoformat(), "window": [first_day, u], "articles": [], "removed": []}
    if args.daily:
        issue["window"] = [min(issue["window"][0], first_day), max(issue["window"][1], u)]
        issue["updated"] = u
    # never repeat a paper that is already in ANY issue, or that the editor removed from one
    known, known_doi = set(), set()
    for f in (root / "data" / "issues").glob("*.json"):
        if f.name == "index.json":
            continue
        other = issue if f == issue_path else load_json(f, {})
        known |= {a["id"] for a in other.get("articles", [])} | set(other.get("removed", []))
        known_doi |= {a.get("doi") for a in other.get("articles", []) if a.get("doi")}
    known |= {a["id"] for a in issue["articles"]} | set(issue.get("removed", []))
    known_doi |= {a.get("doi") for a in issue["articles"] if a.get("doi")}

    figs = FigureFetcher(http, cfg, root)
    venues = VenueChecker(http, cfg, root, online=not args.no_openalex)
    added = rejected = 0
    for rec in merged:
        art = to_article(rec, cfg)
        if art["id"] in known or (art["doi"] and art["doi"] in known_doi):
            continue
        if not venues.ok(rec):
            rejected += 1
            log.debug("venue filter: %s (%s)", rec.get("title", "")[:80], rec.get("journal"))
            continue
        if not args.no_openalex and not rec.get("_openalex_source"):
            enrich_oa(http, rec)
            art["oa"], art["license"] = bool(rec.get("oa")), rec.get("license", "")
        if not args.no_figures:
            art["fig"] = figs.fetch(rec, art["id"])
        issue["articles"].append(art)
        added += 1
    issue["articles"].sort(key=lambda a: (a.get("feature", False), a.get("pick", False), a.get("date", "")), reverse=True)
    log.info("Added %d new articles (issue now has %d); %d dropped by the venue filter", added, len(issue["articles"]), rejected)
    stats["added"], stats["venue_rejected"] = added, rejected
    if not args.dry_run:
        venues.save()
    if not args.dry_run:
        sp = root / "data" / "stats.json"
        hist = load_json(sp, [])
        hist = (hist + [stats])[-120:]
        sp.write_text(json.dumps(hist, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.dry_run:
        json.dump(issue, sys.stdout, indent=2, ensure_ascii=False)
        return 0
    if not existing and added == 0:
        # don't publish an empty issue: the site keeps showing the previous one until papers arrive
        log.info("No new papers; issue %s not created yet", issue["id"])
        figs.save_cache()
        clear_inbox(root, done_inbox)
        print(f"ISSUE_ID={issue['id']}")
        print(f"ISSUE_NUMBER={issue['number']}")
        print("ADDED=0")
        return 0
    issue_path.parent.mkdir(parents=True, exist_ok=True)
    issue_path.write_text(json.dumps(issue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    update_index(root, issue)
    figs.save_cache()
    clear_inbox(root, done_inbox)
    print(f"ISSUE_ID={issue['id']}")
    print(f"ISSUE_NUMBER={issue['number']}")
    print(f"ADDED={added}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "scraper" / "config.yaml"))
    ap.add_argument("--root", default=str(ROOT), help="repository root (where data/ and figs/ live)")
    ap.add_argument("--issue-date", help="YYYY-MM-DD, default: this week's Monday (Singapore time)")
    ap.add_argument("--since", help="window start YYYY-MM-DD (default: issue date − 7 days)")
    ap.add_argument("--until", help="window end YYYY-MM-DD (default: issue date − 1 day)")
    ap.add_argument("--no-figures", action="store_true", help="skip preview-image download")
    ap.add_argument("--daily", action="store_true", help="daily mode: last few days' papers into this week's issue")
    ap.add_argument("--today", help="override today's date for --daily (YYYY-MM-DD, testing)")
    ap.add_argument("--refresh-figures", action="store_true", help="only (re)fetch thumbnails for an existing issue; no crawl")
    ap.add_argument("--force", action="store_true", help="with --refresh-figures: also redo articles that already have a thumbnail")
    ap.add_argument("--no-openalex", action="store_true", help="skip the OpenAlex open-access check")
    ap.add_argument("--dry-run", action="store_true", help="print the issue JSON instead of writing files")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
