import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import curate  # noqa: E402


def issue(n=20):
    return {"id": "2026-11-09", "number": 2, "articles": [{"id": f"a{i}", "title": f"T{i}", "date": f"2026-11-{(i % 9) + 1:02d}"} for i in range(n)]}


def test_picks_feature_and_reviewed():
    iss = issue()
    res = curate.apply_selections(iss, {"picks": [{"id": "a1", "why": "new SRS modality"}, {"id": "a2", "why": "x"}],
                                        "feature": {"id": "a3", "why": "best"}, "reviewed_all": True}, "2026-11-10")
    by = {a["id"]: a for a in iss["articles"]}
    assert res["picks"] == 3 and res["feature"] == "a3"
    assert by["a3"]["feature"] and by["a3"]["pick"] and by["a1"]["why"] == "new SRS modality"
    assert all(a["reviewed"] for a in iss["articles"])
    assert iss["articles"][0]["id"] == "a3"          # feature sorts first


def test_cap_drops_oldest_but_never_feature():
    iss = issue()
    curate.apply_selections(iss, {"picks": [{"id": f"a{i}"} for i in range(10)], "feature": {"id": "a0"}}, "2026-11-10")
    res = curate.apply_selections(iss, {"picks": [{"id": f"a{i}"} for i in range(10, 17)]}, "2026-11-11")
    by = {a["id"]: a for a in iss["articles"]}
    assert res["picks"] == 15 and len(res["dropped"]) == 2
    assert by["a0"]["feature"] and by["a0"]["pick"]                 # feature kept although oldest
    assert all(by[f"a{i}"]["pick"] for i in range(10, 17))           # new ones kept
    assert set(res["dropped"]) <= {f"a{i}" for i in range(1, 10)}


def test_feature_can_change_and_unknown_id_rejected():
    iss = issue()
    curate.apply_selections(iss, {"feature": {"id": "a1"}}, "2026-11-10")
    curate.apply_selections(iss, {"feature": {"id": "a2"}}, "2026-11-11")
    assert [a["id"] for a in iss["articles"] if a.get("feature")] == ["a2"]
    try:
        curate.apply_selections(iss, {"picks": [{"id": "nope"}]}, "2026-11-11")
        assert False
    except SystemExit:
        pass


def test_remove_moves_to_removed_list_but_never_the_feature():
    iss = issue()
    curate.apply_selections(iss, {"feature": {"id": "a1"}}, "2026-11-10")
    res = curate.apply_selections(iss, {"remove": ["a4", "a5", "a1", "zz"]}, "2026-11-11")
    ids = {a["id"] for a in iss["articles"]}
    assert res["removed"] == 2 and "a4" not in ids and "a1" in ids
    assert set(iss["removed"]) == {"a4", "a5"}


def test_notable_tier():
    iss = issue()
    curate.apply_selections(iss, {"notable": ["a2", "a3"], "picks": [{"id": "a4"}]}, "2026-11-10")
    by = {a["id"]: a for a in iss["articles"]}
    assert by["a2"]["tier"] == by["a4"]["tier"] == "notable" and "tier" not in by["a5"]


def test_add_hand_found_paper():
    iss = issue()
    item = {"title": "Supercontinuum-driven broadband Fourier-transform photothermal microspectroscopy",
            "doi": "10.48550/arXiv.2610.32718", "url": "https://arxiv.org/abs/2610.32718", "journal": "arXiv (preprint)",
            "date": "2026-10-23", "authors": "Senyu Wang, Takuro Ideguchi", "abstract": "We develop a photothermal microspectroscopy platform."}
    res = curate.apply_selections(iss, {"add": [item], "picks": [{"id": "a1"}]}, "2026-11-10")
    assert len(res["added"]) == 1
    art = [a for a in iss["articles"] if a["id"] == res["added"][0]][0]
    assert art["tier"] == "notable" and art["reviewed"] and art["doi"] == "10.48550/arxiv.2610.32718"
    res2 = curate.apply_selections(iss, {"add": [item]}, "2026-11-10")
    assert res2["added"] == []
