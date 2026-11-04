"""Offline tests for the news scraper (no network: HTTP is faked).

Domain-specific titles, abstracts and expectations live in site_fixtures.yaml, so the same
suite checks every site built on this pipeline."""
import io
import json
import sys
from pathlib import Path

import pytest
import yaml
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import news_scraper as vs  # noqa: E402

CFG = vs.load_config(HERE.parent / "config.yaml")
F = yaml.safe_load((HERE / "site_fixtures.yaml").read_text(encoding="utf-8"))
CR_DOI = F["crossref"]["doi"].lower()


# ----------------------------------------------------------------- fixtures

EPMC_RESULT = {
    "id": "40000001", "source": "MED", "pmid": "40000001", "doi": "10.1038/s41467-026-00001-x",
    "title": F["epmc"]["title"] + ".",
    "authorString": "Tan A, Lim B, Huang Z.",
    "authorList": {"author": [
        {"fullName": "Tan A", "authorAffiliationDetailsList": {"authorAffiliation": [{"affiliation": "Department of Biomedical Engineering, National University of Singapore, Singapore 117576. a.tan@nus.edu.sg"}]}},
        {"fullName": "Lim B", "authorAffiliationDetailsList": {"authorAffiliation": [{"affiliation": "Institute of Bioengineering and Bioimaging, A*STAR, Singapore"}]}},
        {"fullName": "Huang Z", "authorAffiliationDetailsList": {"authorAffiliation": [{"affiliation": "Department of Biomedical Engineering, National University of Singapore, Singapore"}]}},
    ]},
    "journalInfo": {"journal": {"title": "Nature Communications", "essn": "2041-1723"}},
    "firstPublicationDate": "2026-11-04",
    "abstractText": "<h4>Abstract</h4>" + F["epmc"]["abstract"],
    "isOpenAccess": "Y", "license": "cc by",
    "pubTypeList": {"pubType": ["research-article", "Journal Article"]},
}

EPMC_NOISE = {  # off-topic record: must be excluded
    "id": "40000002", "source": "MED", "doi": "10.1000/noise.1",
    "title": F["noise"]["title"],
    "authorList": {"author": [{"fullName": "Doe J"}]},
    "journalInfo": {"journal": {"title": "NeuroImage"}},
    "firstPublicationDate": "2026-11-03", "abstractText": F["noise"]["abstract"], "isOpenAccess": "N",
}

CROSSREF_ITEM = {
    "DOI": F["crossref"]["doi"],
    "title": [F["crossref"]["title"]],
    "author": [{"given": "Maria", "family": "Rossi", "affiliation": [{"name": "Dipartimento di Fisica, Politecnico di Milano, Milan, Italy"}]}],
    "container-title": [F["crossref"]["journal"]], "ISSN": [F["crossref"]["issn"]],
    "created": {"date-parts": [[2026, 11, 5]]}, "published-online": {"date-parts": [[2026, 11, 5]]},
    "abstract": "<jats:p>" + F["crossref"]["abstract"] + "</jats:p>",
    "license": [{"URL": "http://onlinelibrary.wiley.com/termsAndConditions"}],
}
CROSSREF_DUP = {  # same DOI as the Europe PMC record, should merge away
    "DOI": "10.1038/s41467-026-00001-x", "title": [F["epmc"]["title"]],
    "author": [], "container-title": ["Nature Communications"], "created": {"date-parts": [[2026, 11, 4]]},
    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
}

def oa_work(doi, title, abstract, journal="Light: Science & Applications", date="2026-11-05", insts=("Zhejiang University",)):
    words = abstract.split()
    return {"id": "https://openalex.org/W" + doi[-4:], "doi": "https://doi.org/" + doi, "display_name": title, "publication_date": date, "type": "article",
            "authorships": [{"author": {"display_name": "Li Wei"}, "institutions": [{"display_name": i} for i in insts]}],
            "primary_location": {"source": {"display_name": journal, "issn_l": "2047-7538", "issn": ["2047-7538"], "type": "journal", "id": "https://openalex.org/S1"}},
            "open_access": {"oa_status": "gold"}, "best_oa_location": {"license": "cc-by"},
            "abstract_inverted_index": {w: [i] for i, w in enumerate(words)} if abstract else None}


OA = F["openalex"]
OPENALEX_RESULTS = [
    # paper whose title never names the technique: kept because a strong term appears once in the abstract
    oa_work("10.1038/s41377-026-00001-a", OA["strong_once"]["title"], OA["strong_once"]["abstract"]),
    # same DOI as the Crossref record; OpenAlex supplies its own abstract
    oa_work(CR_DOI, F["crossref"]["title"], OA["dup_abstract"], journal=F["crossref"]["journal"]),
    # paper that mentions the field only in passing: must be dropped
    oa_work("10.1016/j.mat.2026.0001", OA["passing"]["title"], OA["passing"]["abstract"]),
    # old paper re-indexed: must be dropped by the age guard
    oa_work("10.1000/old.1", OA["old"]["title"], OA["old"]["abstract"], date="2025-01-10"),
]

CORE_ITEM = {  # core journal: kept although the title has no keyword
    "DOI": F["core"]["doi"], "title": [F["core"]["title"]],
    "author": [{"given": "Ana", "family": "Costa"}], "container-title": [F["core"]["journal"]], "ISSN": [F["core"]["issn"]],
    "created": {"date-parts": [[2026, 11, 5]]}, "license": [],
}

ARXIV_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2611.01234v1</id>
    <published>2026-11-06T10:00:00Z</published>
    <title>{F["arxiv"]["title"]}</title>
    <summary>{F["arxiv"]["summary"]}</summary>
    <author><name>Ana Silva</name><arxiv:affiliation>University of Queensland</arxiv:affiliation></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2610.00001v2</id>
    <published>2026-10-01T10:00:00Z</published>
    <title>{F["arxiv"]["old_title"]}</title>
    <summary>{F["arxiv"]["old_title"]}</summary>
    <author><name>X Y</name></author>
  </entry>
</feed>"""

ARTICLE_HTML = """<html><head>
<meta property="og:title" content="paper">
<meta property="og:image" content="/figures/fig1.png">
</head><body></body></html>"""
COVER_HTML = """<html><head><meta name="twitter:image" content="https://cdn.example.org/jrs/cover_image.jpg"></head></html>"""


def png_bytes(w=600, h=400, color=(30, 120, 160)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


class FakeResp:
    def __init__(self, url, body=b"", ctype="text/html", status=200):
        self.url, self.status_code = url, status
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode("utf-8", "ignore")
        self.headers = {"Content-Type": ctype}

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    calls: list = []

    def __init__(self, cfg):
        self.cfg = cfg

    def get(self, url, check_robots=False, params=None, **kw):
        FakeHttp.calls.append((url, params))
        if "europepmc" in url:
            q = (params or {}).get("query", "")
            if q.startswith("DOI:"):
                return FakeResp(url, json.dumps({"resultList": {"result": []}}), "application/json")
            if (params or {}).get("cursorMark") == "*":
                return FakeResp(url, json.dumps({"nextCursorMark": "AAA", "resultList": {"result": [EPMC_RESULT, EPMC_NOISE]}}), "application/json")
            return FakeResp(url, json.dumps({"nextCursorMark": "AAA", "resultList": {"result": []}}), "application/json")
        if url == "https://api.openalex.org/works":
            if "cursor" in (params or {}) and params["cursor"] != "*":
                return FakeResp(url, json.dumps({"meta": {"next_cursor": None}, "results": []}), "application/json")
            return FakeResp(url, json.dumps({"meta": {"next_cursor": "NEXT"}, "results": OPENALEX_RESULTS}), "application/json")
        if "api.crossref.org/works" in url and "issn:" in (params or {}).get("filter", ""):
            return FakeResp(url, json.dumps({"message": {"items": [CORE_ITEM]}}), "application/json")
        if "api.crossref.org/works/" in url:   # single-DOI lookup (inbox)
            item = dict(CROSSREF_ITEM, DOI="10.1364/OE.999999", title=["Inbox paper: " + F["inbox_title"]])
            return FakeResp(url, json.dumps({"message": item}), "application/json")
        if "api.crossref.org/works" in url:
            return FakeResp(url, json.dumps({"message": {"items": [CROSSREF_ITEM, CROSSREF_DUP]}}), "application/json")
        if "arxiv.org/api" in url:
            return FakeResp(url, ARXIV_XML, "application/atom+xml")
        if url == "https://doi.org/10.1038/s41467-026-00001-x":
            return FakeResp("https://www.nature.com/articles/s41467-026-00001-x", ARTICLE_HTML)
        if url == "https://www.nature.com/figures/fig1.png":
            return FakeResp(url, png_bytes(), "image/png")
        if url == "https://doi.org/" + CR_DOI:
            return FakeResp("https://journals.example.org/doi/" + CR_DOI, COVER_HTML)
        if url == "https://cdn.example.org/jrs/cover_image.jpg":
            return FakeResp(url, png_bytes(300, 400, (200, 60, 60)), "image/jpeg")
        return None


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "data" / "issues").mkdir(parents=True)
    (tmp_path / "data" / "inbox.txt").write_text("# submissions\n10.1364/OE.999999\n", encoding="utf-8")
    monkeypatch.setattr(vs, "Http", FakeHttp)
    FakeHttp.calls = []
    return tmp_path


def find(arts, key):
    return next(a for a in arts if key in a["title"])


# ----------------------------------------------------------------- unit tests

def test_parse_europepmc_fields():
    r = vs.parse_europepmc(EPMC_RESULT)
    assert r["title"] == F["epmc"]["title"]
    assert r["doi"] == "10.1038/s41467-026-00001-x"
    assert r["oa"] is True and r["license"] == "CC BY"
    assert r["journal"] == "Nature Communications"
    assert F["epmc"]["abstract"][:30] in r["abstract"] and "Abstract" not in r["abstract"][:9]


def test_institutions_strip_departments_and_emails():
    r = vs.parse_europepmc(EPMC_RESULT)
    inst = vs.institutions_from(r["affiliations"])
    assert inst[0] == "National University of Singapore"
    assert any("A*STAR" in i or "Institute of Bioengineering" in i for i in inst)
    assert len(inst) == 2  # NUS de-duplicated


@pytest.mark.parametrize("case", F["relevance"], ids=lambda c: c["title"][:40])
def test_relevance_filters(case):
    assert vs.is_relevant(vs.parse_europepmc(EPMC_RESULT), CFG)
    assert not vs.is_relevant(vs.parse_europepmc(EPMC_NOISE), CFG)
    rec = {"title": case["title"], "abstract": case.get("abstract", ""), "journal": case.get("journal", "")}
    assert vs.is_relevant(rec, CFG) is case["keep"]


def test_correction_notices_are_skipped():
    assert not vs.is_relevant({"title": "Correction to: " + F["epmc"]["title"], "abstract": ""}, CFG)


def test_categories_and_glyph():
    r = vs.parse_europepmc(EPMC_RESULT)
    cats = vs.categorize(r, CFG)
    for c in F["epmc"]["categories"]:
        assert c in cats, cats
    assert vs.glyph_for(cats, CFG) == F["epmc"]["glyph"]
    first, second = F["epmc"]["order"]
    assert cats.index(first) < cats.index(second)


def test_crossref_and_arxiv_parsing():
    c = vs.parse_crossref(CROSSREF_ITEM)
    assert c["doi"] == CR_DOI and c["date"] == "2026-11-05" and not c["oa"]
    assert vs.parse_crossref(CROSSREF_DUP)["license"] == "CC BY 4.0"
    a = vs.parse_arxiv(ARXIV_XML, "2026-11-02", "2026-11-08")
    assert len(a) == 1 and a[0]["url"] == "https://arxiv.org/abs/2611.01234"


def test_merge_dedupes_by_doi_and_fills_license():
    recs = [vs.parse_crossref(CROSSREF_DUP), vs.parse_europepmc(EPMC_RESULT), vs.parse_crossref(CROSSREF_ITEM)]
    merged = vs.merge_records(recs)
    assert len(merged) == 2
    assert merged[0]["source"] == "europepmc"


def test_trim_summary_sentence_boundary():
    s = "First sentence is here. " * 60
    t = vs.trim_summary(s, 200)
    assert len(t) <= 205 and t.endswith("…") and ". …" in t


def test_preview_image_extraction_and_cover_detection():
    assert vs.extract_preview_image(ARTICLE_HTML, "https://www.nature.com/articles/x") == "https://www.nature.com/figures/fig1.png"
    url = vs.extract_preview_image(COVER_HTML, "https://onlinelibrary.wiley.com/doi/x")
    assert vs.looks_like_cover(url)
    assert vs.extract_preview_image("<html></html>", "https://x.org") is None


def test_save_thumbnail_rejects_logos(tmp_path):
    assert not vs.save_thumbnail(png_bytes(64, 64), tmp_path / "a.jpg", CFG)
    assert vs.save_thumbnail(png_bytes(1600, 1000), tmp_path / "b.jpg", CFG)
    im = Image.open(tmp_path / "b.jpg")
    assert im.size[0] <= 400 and im.size[1] <= 280


# ----------------------------------------------------------------- end-to-end (fake network)

def run_cli(repo, *extra):
    return vs.main(["--root", str(repo), "--issue-date", "2026-11-09", *extra])


def test_end_to_end_issue_with_figure_cover_and_inbox(repo):
    assert run_cli(repo) == 0
    issue = json.loads((repo / "data/issues/2026-11-09.json").read_text())
    assert issue["window"] == ["2026-11-02", "2026-11-08"]
    titles = [a["title"] for a in issue["articles"]]
    assert F["epmc"]["title"] in titles
    assert F["crossref"]["title"] in titles
    assert F["arxiv"]["title"] in titles                    # arXiv
    assert any("Inbox paper" in t for t in titles)          # submitted DOI
    assert F["noise"]["title"] not in titles
    ep = find(issue["articles"], F["epmc"]["title"])
    assert ep["fig"]["kind"] == "figure" and (repo / ep["fig"]["src"]).exists()
    assert "CC BY" in ep["fig"]["credit"]
    cr = find(issue["articles"], F["crossref"]["title"])
    assert cr["fig"]["kind"] == "cover" and cr["fig"]["src"].startswith("covers/")
    ax = find(issue["articles"], F["arxiv"]["title"])
    assert ax["fig"] is None and ax["glyph"] == F["arxiv"]["glyph"]
    idx = json.loads((repo / "data/issues/index.json").read_text())
    assert idx["issues"][0]["id"] == "2026-11-09" and idx["issues"][0]["number"] == 1
    assert "10.1364" not in (repo / "data/inbox.txt").read_text()   # processed DOI removed
    assert json.loads((repo / "covers/index.json").read_text())[F["crossref"]["journal"].lower()]["kind"] == "cover"


def test_rerun_keeps_editor_changes_and_removed(repo):
    run_cli(repo, "--no-figures")
    p = repo / "data/issues/2026-11-09.json"
    issue = json.loads(p.read_text())
    ep = find(issue["articles"], F["epmc"]["title"])
    ep["feature"] = True
    ep["summary"] = "Editor's own summary."
    victim = find(issue["articles"], F["crossref"]["title"])
    issue["articles"].remove(victim)
    issue["removed"].append(victim["id"])
    p.write_text(json.dumps(issue))
    run_cli(repo, "--no-figures")
    again = json.loads(p.read_text())
    ep2 = find(again["articles"], F["epmc"]["title"])
    assert ep2["feature"] and ep2["summary"] == "Editor's own summary."
    assert not any(F["crossref"]["title"] in a["title"] for a in again["articles"])
    assert again["articles"][0]["id"] == ep2["id"]  # feature sorts first


class PolicyHttp(FakeHttp):
    """Extra routes for the OA-figure / paywalled-cover policy."""

    def get(self, url, check_robots=False, params=None, **kw):
        routes = {
            # paywalled ACS paper: landing image is a landscape TOC graphic (must NOT be used)
            "https://doi.org/10.1021/acs.analchem.6c99999": ("https://pubs.acs.org/doi/10.1021/acs.analchem.6c99999", '<meta property="og:image" content="https://pubs.acs.org/cms/toc.jpg">', "text/html"),
            "https://pubs.acs.org/cms/toc.jpg": (url, png_bytes(600, 300), "image/jpeg"),
            "https://api.openalex.org/works/doi:10.1021/acs.analchem.6c99999": (url, json.dumps({"open_access": {"is_oa": False, "oa_status": "closed"}, "primary_location": {"source": {"id": "https://openalex.org/S123", "issn_l": "0003-2700"}}}), "application/json"),
            "https://api.openalex.org/sources/S123": (url, json.dumps({"homepage_url": "https://pubs.acs.org/journal/ancham"}), "application/json"),
            "https://pubs.acs.org/journal/ancham": (url, '<meta property="og:image" content="https://pubs.acs.org/ancham-img.jpg">', "text/html"),
            "https://pubs.acs.org/ancham-img.jpg": (url, png_bytes(300, 400, (10, 60, 120)), "image/jpeg"),
            # OA status discovered only through OpenAlex
            "https://api.openalex.org/works/doi:10.1038/s41467-026-00001-x": (url, json.dumps({"open_access": {"is_oa": True, "oa_status": "gold"}, "best_oa_location": {"license": "cc-by"}, "primary_location": {"source": {"id": "https://openalex.org/S9", "issn_l": "2041-1723"}}}), "application/json"),
            # OA page without og:image but with figures in the HTML (Light: Advanced Manufacturing style)
            "https://doi.org/10.37188/lam.2026.999": ("https://www.light-am.com/article/doi/10.37188/lam.2026.999", '<html><body><img src="/img/logo.png"><div class="figure"><img class="figure_img" src="/files/LAM-1.jpg"></div></body></html>', "text/html"),
            "https://www.light-am.com/files/LAM-1.jpg": (url, png_bytes(800, 500), "image/jpeg"),
            # paywalled Springer paper: landscape figure on the article page; cover on the journal homepage is a 92-px og:image
            "https://doi.org/10.1007/s10439-026-99999-x": ("https://link.springer.com/article/10.1007/s10439-026-99999-x", '<meta property="og:image" content="https://media.springernature.com/lw685/springer-static/image/art/fig1.png">', "text/html"),
            "https://media.springernature.com/lw685/springer-static/image/art/fig1.png": (url, png_bytes(685, 400), "image/png"),
            "https://api.openalex.org/works/doi:10.1007/s10439-026-99999-x": (url, json.dumps({"open_access": {"is_oa": False, "oa_status": "closed"}, "primary_location": {"source": {"id": "https://openalex.org/S77", "issn_l": "0090-6964"}}}), "application/json"),
            "https://api.openalex.org/sources/S77": (url, json.dumps({"homepage_url": "https://link.springer.com/journal/10439"}), "application/json"),
            "https://link.springer.com/journal/10439": (url, '<meta property="og:image" content="https://media.springernature.com/w92/springer-static/cover/journal/10439.jpg">', "text/html"),
            "https://media.springernature.com/w316/springer-static/cover/journal/10439.jpg": (url, png_bytes(316, 420, (90, 20, 20)), "image/jpeg"),
            # paywalled paper with no cover anywhere
            "https://doi.org/10.9999/nocover": ("https://example.org/nocover", "<html></html>", "text/html"),
        }
        if url in routes:
            final, body, ctype = routes[url]
            return FakeResp(final, body, ctype)
        return super().get(url, check_robots, params, **kw)


def paywalled_acs():
    return {"source": "crossref", "title": "SERS assay", "doi": "10.1021/acs.analchem.6c99999", "url": "https://doi.org/10.1021/acs.analchem.6c99999",
            "journal": "Analytical Chemistry", "oa": False, "license": ""}


def test_paywalled_paper_gets_journal_cover_never_its_figure(tmp_path):
    f = vs.FigureFetcher(PolicyHttp(CFG), CFG, tmp_path)
    rec = vs.enrich_oa(f.http, paywalled_acs())
    assert rec["oa"] is False and rec["_openalex_source"] == "S123"
    fig = f.fetch(rec, "acs1")
    assert fig["kind"] == "cover" and fig["src"].startswith("covers/analytical-chemistry")
    assert not (tmp_path / "figs" / "acs1.jpg").exists()
    # second paper from the same journal reuses the cached cover without new requests
    FakeHttp.calls = []
    again = f.fetch(dict(paywalled_acs(), doi="10.1021/x", url=""), "acs2")
    assert again["src"] == fig["src"] and not any("ancham-img" in c[0] for c in FakeHttp.calls)


def test_paywalled_paper_without_any_cover_falls_back_to_glyph(tmp_path):
    f = vs.FigureFetcher(PolicyHttp(CFG), CFG, tmp_path)
    rec = {"source": "crossref", "title": "x", "doi": "10.9999/nocover", "url": "https://doi.org/10.9999/nocover", "journal": "Obscure Letters", "oa": False}
    assert f.fetch(rec, "n1") is None


def test_open_access_paper_gets_its_own_figure_and_openalex_upgrades_oa(tmp_path):
    f = vs.FigureFetcher(PolicyHttp(CFG), CFG, tmp_path)
    rec = vs.parse_europepmc(dict(EPMC_RESULT, isOpenAccess="N", license=""))
    assert rec["oa"] is False
    vs.enrich_oa(f.http, rec)
    assert rec["oa"] is True and rec["license"] == "CC BY"
    fig = f.fetch(rec, "oa1")
    assert fig["kind"] == "figure" and "CC BY" in fig["credit"] and (tmp_path / "figs/oa1.jpg").exists()


def test_mode_all_uses_figure_even_for_paywalled(tmp_path):
    cfg = vs.load_config(HERE.parent / "config.yaml")
    cfg["figures"]["mode"] = "all"
    f = vs.FigureFetcher(PolicyHttp(cfg), cfg, tmp_path)
    assert f.fetch(paywalled_acs(), "acs3")["kind"] == "figure"


def test_open_access_page_without_og_image_uses_first_figure(tmp_path):
    f = vs.FigureFetcher(PolicyHttp(CFG), CFG, tmp_path)
    rec = {"source": "crossref", "title": "Diamond SERS", "doi": "10.37188/lam.2026.999", "url": "https://doi.org/10.37188/lam.2026.999", "journal": "Light: Advanced Manufacturing", "oa": True, "license": "CC BY 4.0"}
    fig = f.fetch(rec, "lam")
    assert fig["kind"] == "figure" and (tmp_path / "figs/lam.jpg").exists()


def test_first_figure_ignores_related_article_thumbnails():
    page = ('<figure><img src="https://media.springernature.com/w72/springer-static/cover-hires/journal/12911"></figure>'
            '<figure><img src="https://media.springernature.com/w215h120/springer-static/image/art%3A10.1186%2Fs40644-025-00921-w/MediaObjects/Fig1.png"></figure>')
    assert vs.extract_first_figure(page, "https://link.springer.com/x", "10.1186/s12911-026-03922-2") is None
    own = page + '<figure><img src="https://media.springernature.com/lw685/springer-static/image/art%3A10.1186%2Fs12911-026-03922-2/MediaObjects/Fig1.png"></figure>'
    assert "s12911-026-03922-2" in vs.extract_first_figure(own, "https://link.springer.com/x", "10.1186/s12911-026-03922-2")


def test_springer_paywalled_gets_upgraded_homepage_cover_not_figure(tmp_path):
    f = vs.FigureFetcher(PolicyHttp(CFG), CFG, tmp_path)
    rec = vs.enrich_oa(f.http, {"source": "crossref", "title": "Bone SRS", "doi": "10.1007/s10439-026-99999-x", "url": "https://doi.org/10.1007/s10439-026-99999-x", "journal": "Annals of Biomedical Engineering", "oa": False})
    fig = f.fetch(rec, "abme")
    assert fig["kind"] == "cover" and not (tmp_path / "figs/abme.jpg").exists()
    assert vs.upgrade_image_url("https://media.springernature.com/w92/springer-static/cover/journal/10439.jpg").endswith("/w316/springer-static/cover/journal/10439.jpg")


def test_refresh_finds_missing_doi_by_title(repo, monkeypatch):
    monkeypatch.setattr(vs, "Http", PolicyHttp)
    issue = {"id": "2026-11-02", "number": 1, "date": "2026-11-02", "window": ["a", "b"], "removed": [], "articles": [
        {"id": "cr", "title": F["crossref"]["title"], "doi": None, "url": "https://journals.example.org/", "journal": F["crossref"]["journal"], "oa": False, "license": "", "fig": None}]}
    p = repo / "data/issues/2026-11-02.json"
    p.write_text(json.dumps(issue))
    vs.main(["--root", str(repo), "--issue-date", "2026-11-02", "--refresh-figures"])
    art = json.loads(p.read_text())["articles"][0]
    assert art["doi"] == CR_DOI and art["url"] == "https://doi.org/" + CR_DOI
    assert art["fig"]["kind"] == "cover"     # the article page exposes a cover image


def test_refresh_figures_for_existing_issue(repo, monkeypatch):
    monkeypatch.setattr(vs, "Http", PolicyHttp)
    issue = {"id": "2026-11-02", "number": 1, "date": "2026-11-02", "window": ["2026-10-26", "2026-11-01"], "removed": [], "articles": [
        {"id": "oa-paper", "title": "OA paper", "doi": "10.1038/s41467-026-00001-x", "url": "https://doi.org/10.1038/s41467-026-00001-x", "journal": "Nature Communications", "oa": False, "license": "", "fig": None},
        {"id": "pw-paper", "title": "Paywalled paper", "doi": "10.1021/acs.analchem.6c99999", "url": "https://doi.org/10.1021/acs.analchem.6c99999", "journal": "Analytical Chemistry", "oa": False, "license": "", "fig": None},
    ]}
    p = repo / "data/issues/2026-11-02.json"
    p.write_text(json.dumps(issue))
    assert vs.main(["--root", str(repo), "--issue-date", "2026-11-02", "--refresh-figures"]) == 0
    out = {a["id"]: a for a in json.loads(p.read_text())["articles"]}
    assert out["oa-paper"]["oa"] is True and out["oa-paper"]["fig"]["kind"] == "figure"
    assert out["pw-paper"]["fig"]["kind"] == "cover"


# ----------------------------------------------------------------- site directions

@pytest.mark.parametrize("title,abstract,keep,cat,glyph", [tuple(x) for x in F["directions"]], ids=lambda v: str(v)[:40])
def test_site_directions(title, abstract, keep, cat, glyph):
    rec = {"title": title, "abstract": abstract}
    assert vs.is_relevant(rec, CFG) is keep, title
    if keep:
        cats = vs.categorize(rec, CFG)
        assert cat in cats, cats
        if glyph:
            assert vs.glyph_for(cats, CFG) == glyph


# ----------------------------------------------------------------- daily mode

def test_daily_mode_adds_to_this_weeks_issue_and_never_repeats_across_issues(repo):
    assert vs.main(["--root", str(repo), "--daily", "--today", "2026-11-11", "--no-figures"]) == 0   # Wednesday
    wk1 = json.loads((repo / "data/issues/2026-11-09.json").read_text())
    assert wk1["window"][0] == "2026-11-09" and wk1["updated"] == "2026-11-11"
    n1 = len(wk1["articles"])
    assert n1 >= 3
    # next day: same papers come back from the APIs -> nothing new, window extends
    vs.main(["--root", str(repo), "--daily", "--today", "2026-11-12", "--no-figures"])
    wk1b = json.loads((repo / "data/issues/2026-11-09.json").read_text())
    assert len(wk1b["articles"]) == n1 and wk1b["window"][1] == "2026-11-12"
    # following Monday: a new issue starts, but papers already published last week are not repeated
    vs.main(["--root", str(repo), "--daily", "--today", "2026-11-16", "--no-figures"])
    assert not (repo / "data/issues/2026-11-16.json").exists()      # no empty issue published
    idx = json.loads((repo / "data/issues/index.json").read_text())
    assert [i["id"] for i in idx["issues"]] == ["2026-11-09"]


# ----------------------------------------------------------------- recall safeguards

def test_recall_safeguards_end_to_end(repo):
    assert vs.main(["--root", str(repo), "--daily", "--today", "2026-11-11", "--no-figures"]) == 0
    arts = json.loads((repo / "data/issues/2026-11-09.json").read_text())["articles"]
    titles = {a["title"]: a for a in arts}
    so = titles.get(OA["strong_once"]["title"])
    assert so is not None and OA["strong_once"]["category"] in so["categories"]
    assert so["institutions"] == ["Zhejiang University"]                      # institutions straight from OpenAlex
    if CFG["_core_issn"] or CFG["_core_titles"]:
        assert F["core"]["title"] in titles                                    # core journal
    assert OA["passing"]["title"] not in titles                                # field mentioned only in passing
    assert OA["old"]["title"] not in titles                                    # older than max_age_days
    assert OA["dup_abstract"][:20] in titles[F["crossref"]["title"]]["summary"] or F["crossref"]["abstract"][:20] in titles[F["crossref"]["title"]]["summary"]
    # Europe PMC was searched by entry date in daily mode
    q = next(c[1]["query"] for c in FakeHttp.calls if "europepmc" in c[0] and c[1] and "query" in c[1] and not c[1]["query"].startswith("DOI"))
    assert "CREATION_DATE:[2026-11-08 TO 2026-11-11]" in q and "KW:" in q
    # OpenAlex looked back 30 days
    oa = next(c[1]["filter"] for c in FakeHttp.calls if c[0] == "https://api.openalex.org/works")
    assert "from_publication_date:2026-10-12" in oa
    stats = json.loads((repo / "data/stats.json").read_text())[-1]
    assert stats["sources"]["openalex"] == 4 * len(CFG["openalex_terms"]) and stats["added"] == len(arts)


@pytest.mark.parametrize("title,abstract,keep", [tuple(x) for x in F["once"]], ids=lambda v: str(v)[:40])
def test_specific_term_once_is_enough_but_generic_mention_is_not(title, abstract, keep):
    assert vs.is_relevant({"title": title, "abstract": abstract}, CFG) is keep


def test_blocked_publisher_is_skipped_after_two_403s(tmp_path):
    class BlockHttp(FakeHttp):
        def get(self, url, check_robots=False, params=None, **kw):
            FakeHttp.calls.append((url, params))
            self.last_url = url.replace("https://doi.org/", "https://www.sciencedirect.com/")
            self.last_status = 403
            return None
    FakeHttp.calls = []
    f = vs.FigureFetcher(BlockHttp(CFG), CFG, tmp_path)
    for i in range(6):
        rec = {"source": "crossref", "title": f"p{i}", "doi": f"10.1016/j.x.{i}", "url": f"https://doi.org/10.1016/j.x.{i}", "journal": "Talanta", "oa": True}
        assert f.fetch(rec, f"e{i}") is None
    landing = [c for c in FakeHttp.calls if c[0].startswith("https://doi.org/10.1016/")]
    assert len(landing) == 2          # after two 403s the prefix is skipped


def test_extra_topics():
    for t, a in F["extra_topics"]["yes"]:
        assert vs.is_relevant({"title": t, "abstract": a}, CFG), t
    for t, a in F["extra_topics"]["no"]:
        assert not vs.is_relevant({"title": t, "abstract": a}, CFG), t


def test_clutter_filters_and_venues(tmp_path):
    cfg = CFG
    for t in F["clutter"]["drop"]:
        assert not vs.is_relevant({"title": t, "abstract": ""}, cfg), t
    for t in F["clutter"]["keep"]:
        assert vs.is_relevant({"title": t, "abstract": ""}, cfg), t

    class H:
        calls = 0
        def get(self, url):
            H.calls += 1
            class R:
                def json(self_inner):
                    return {"summary_stats": {"h_index": 55 if "1234-5678" in url else 3}}
            return R()
    v = vs.VenueChecker(H(), cfg, tmp_path, online=True)
    (tmp_path / "data").mkdir()
    assert v.ok({"doi": "10.1021/acs.analchem.1", "journal": "Analytical Chemistry"})
    assert v.ok({"doi": "", "journal": "arXiv (preprint)"})
    assert not v.ok({"doi": "10.21203/rs.3", "journal": "Research Square"})
    assert not v.ok({"doi": "10.1051/e3sconf/1", "journal": "E3S Web of Conferences"})
    assert v.ok({"doi": "10.9999/x", "journal": "Good Society Journal", "issns": ["1234-5678"]})
    assert not v.ok({"doi": "10.9998/y", "journal": "Unknown Journal", "issns": ["0000-0001"]})
    n = H.calls
    assert v.ok({"doi": "10.9999/z", "journal": "Good Society Journal", "issns": ["1234-5678"]}) and H.calls == n  # cached
    v.save()
    assert json.loads((tmp_path / "data" / "venues.json").read_text())["issn:1234-5678"] == 55


def test_routine_applications_dropped_method_papers_kept():
    for t in F["routine"]["drop"]:
        assert not vs.is_relevant({"title": t, "abstract": ""}, CFG), t
    for t in F["routine"]["keep"]:
        assert vs.is_relevant({"title": t, "abstract": ""}, CFG), t


def test_technique_papers_always_included_and_promoted():
    T = F["technique"]
    rec = {"title": T["strong"]["title"], "abstract": T["strong"]["abstract"]}
    assert vs.is_technique(rec, CFG, strong=True) and vs.is_relevant(rec, CFG)
    rec2 = {"title": T["one_mention"]["title"], "abstract": T["one_mention"]["abstract"]}
    assert vs.is_relevant(rec2, CFG)
    art = vs.to_article({**rec, "date": "2026-11-03", "authors": [], "doi": "10.1/x"}, CFG)
    assert art.get("tier") == "notable"
    rec3 = {"title": T["routine"]["title"], "abstract": T["routine"]["abstract"]}
    assert not vs.is_technique(rec3, CFG) and not vs.is_relevant(rec3, CFG)
    rec4 = {"title": T["excluded"]["title"], "abstract": T["excluded"]["abstract"]}
    assert not vs.is_relevant(rec4, CFG)


def test_standalone_page_embeds_all_issues(tmp_path):
    import build_standalone
    root = HERE.parent.parent
    for f in ["index.html", "data/issues/index.json", "data/meetings.json"]:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes((root / f).read_bytes())
    for meta in json.loads((root / "data/issues/index.json").read_text(encoding="utf-8"))["issues"]:
        (tmp_path / f"data/issues/{meta['id']}.json").write_bytes((root / f"data/issues/{meta['id']}.json").read_bytes())
    out = build_standalone.build(tmp_path)
    html = out.read_text(encoding="utf-8")
    assert "window.FEMTO_DATA = {" in html and "<!--FEMTO_DATA-->" not in html
    assert html.count("</script>") == (root / "index.html").read_text(encoding="utf-8").count("</script>") + 1
