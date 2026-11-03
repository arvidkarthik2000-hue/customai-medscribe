"""RxNav and openFDA paths, using recorded response shapes through httpx.MockTransport."""
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-drugs-"))

import httpx
import pytest

from app.drugs import net, openfda, rxnav
from app.drugs.engine import check_visit

FIX = Path(__file__).resolve().parent.parent / "app" / "drugs" / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


class Recorder:
    def __init__(self, routes, fail_hosts=()):
        self.routes = routes
        self.fail_hosts = set(fail_hosts)
        self.calls = []

    def __call__(self, request: httpx.Request):
        self.calls.append(str(request.url))
        if request.url.host in self.fail_hosts:
            raise httpx.ConnectError("refused", request=request)
        for match, payload in self.routes:
            if match(request):
                if payload is None:
                    return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
                return httpx.Response(200, json=payload)
        if request.url.host == "api.fda.gov":
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        return httpx.Response(200, json={})


@pytest.fixture
def online(monkeypatch):
    monkeypatch.delenv("DRUGS_OFFLINE", raising=False)
    net.reset()
    net.clear_cache()
    yield
    net.transport = None
    net.reset()
    net.clear_cache()


def use(recorder):
    net.transport = httpx.MockTransport(recorder)
    net.reset()


def test_misspelling_resolved_by_rxnorm_and_display(online):
    rec = Recorder([
        (lambda r: r.url.path.endswith("/approximateTerm.json"), load("rxnav_approximate_lisinoprel.json")),
        (lambda r: r.url.path.endswith("/drugs.json"), load("rxnav_drugs_lisinopril.json")),
    ])
    use(rec)
    r = check_visit([{"id": "m1", "name": "lisinoprel", "dose": "20", "unit": "mg", "frequency": "daily", "action": "start"}], {})
    m = r["medications"][0]["match"]
    assert m["status"] == "matched" and m["ingredient"] == "lisinopril"
    assert m["rxcui"] == "29046" and m["source"] == "RxNorm"
    assert m["display"] == "lisinopril 20 MG Oral Tablet"
    rx = next(s for s in r["sources_checked"] if "RxNorm" in s["name"])
    assert rx["status"] == "ok"


def test_weak_rxnorm_candidates_are_ambiguous(online):
    use(Recorder([(lambda r: r.url.path.endswith("/approximateTerm.json"), load("rxnav_approximate_zzunknown.json"))]))
    r = check_visit([{"id": "m1", "name": "zolpitrizamide", "action": "start"}], {})
    m = r["medications"][0]["match"]
    assert m["status"] == "ambiguous" and m["candidates"]


def test_label_mention_card_for_uncurated_pair(online):
    def is_label(generic):
        return lambda r: r.url.path.endswith("/drug/label.json") and generic in r.url.params.get("search", "")
    use(Recorder([(is_label("metoprolol"), load("openfda_label_metoprolol.json"))]))
    r = check_visit([{"id": "m1", "name": "fluoxetine", "dose": "20", "unit": "mg", "frequency": "daily", "action": "start"}],
                    {"medications": [{"name": "metoprolol"}]})
    c = next(c for c in r["cards"] if c.get("label_mention"))
    assert c["indicator"] == "info" and "fluoxetine" in c["detail"].lower()
    assert c["source"]["date"] == "2024-03-15" and "setid=" in c["source"]["url"]
    assert c["grading"] is None and "not graded" in c["detail"]


def test_renal_card_includes_live_label_text(online):
    use(Recorder([(lambda r: r.url.path.endswith("/drug/label.json") and "metformin" in r.url.params.get("search", ""), load("openfda_label_metformin.json"))]))
    r = check_visit([{"id": "m1", "name": "metformin", "dose": "500", "unit": "mg", "frequency": "daily", "action": "start"}], {"egfr": 27})
    c = next(c for c in r["cards"] if c["category"] == "renal")
    assert "Label text (effective 2025-01-02)" in c["detail"]
    assert c["sources"][0]["url"].endswith("demo2")


def test_unreachable_sources_degrade_and_breaker_limits_calls(online):
    rec = Recorder([], fail_hosts={"rxnav.nlm.nih.gov", "api.fda.gov"})
    use(rec)
    rows = [{"id": f"m{i}", "name": n, "action": "start"} for i, n in enumerate(["zzzunknownone", "zzzunknowntwo", "fluoxetine"])]
    r = check_visit(rows, {"medications": [{"name": "metoprolol"}]}, timeout_s=8)
    st = {s["name"]: s["status"] for s in r["sources_checked"]}
    assert st["RxNorm (NLM RxNav)"] == "unavailable"
    rx_calls = [c for c in rec.calls if "rxnav" in c]
    assert len(rx_calls) <= 3  # the breaker stops repeated attempts
    assert r["elapsed_s"] < 8


def test_clients_parse_documented_shapes(online):
    use(Recorder([
        (lambda r: r.url.path.endswith("/rxcui.json"), {"idGroup": {"name": "warfarin", "rxnormId": ["11289"]}}),
        (lambda r: r.url.path.endswith("/related.json"), {"relatedGroup": {"rxcui": "855332", "termType": ["IN", "MIN"], "conceptGroup": [{"tty": "IN", "conceptProperties": [{"rxcui": "11289", "name": "warfarin", "tty": "IN"}]}, {"tty": "MIN"}]}}),
        (lambda r: r.url.path.endswith("/spellingsuggestions.json"), {"suggestionGroup": {"name": "ambienn", "suggestionList": {"suggestion": ["ambien", "Ambien CR"]}}}),
    ]))
    assert rxnav.rxcui_by_name("warfarin") == "11289"
    assert rxnav.ingredients("855332") == [{"rxcui": "11289", "name": "warfarin", "tty": "IN"}]
    assert rxnav.spelling_suggestions("ambienn") == ["ambien", "Ambien CR"]


def test_openfda_label_not_found_returns_none(online):
    use(Recorder([]))
    assert openfda.label("lisinopril") is None


def test_offline_env_never_calls_network(monkeypatch):
    monkeypatch.setenv("DRUGS_OFFLINE", "1")
    net.reset()
    rec = Recorder([])
    use(rec)
    with pytest.raises(net.SourceUnavailable):
        rxnav.approximate_term("never")
    assert rec.calls == []
    net.transport = None
