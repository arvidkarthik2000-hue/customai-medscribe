"""Real Part D formulary loader (app/drugs/partd.py) against a tiny synthetic SPUF.

The synthetic package copies the real layout: an outer zip of nested zips, pipe-delimited
files with the CMS record-layout column names (one file with a lower-case header), a
two-part formulary member, decoy insulin/excluded/pharmacy/pricing members, and a big
pharmacy member that must never be fetched. RxNav and the data.gov catalog are mocked.
"""
import io
import os
import random
import re
import tempfile
import time
import zipfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-partd-"))

import httpx
import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app import auth
from app.drugs import formulary, net, partd
from app.drugs.engine import check_visit
from app.drugs.routes import router

ZIP_URL = "https://data.cms.example/sites/default/files/2026-01/abc/SPUF_2026_20260107.zip"
GENERICS = ["lisinopril", "hydrochlorothiazide", "amoxicillin", "amoxicillin-clavulanate", "dapagliflozin", "empagliflozin", "aspirin"]

# ------------------------------------------------------------------ synthetic SPUF


def _psv(header, rows):
    return "\r\n".join(["|".join(header)] + ["|".join(str(c) for c in r) for r in rows]) + "\r\n"


def _zip(members, compression=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as z:
        for name, data in members:
            z.writestr(name, data)
    return buf.getvalue()


PLAN_HEADER = ["CONTRACT_ID", "PLAN_ID", "SEGMENT_ID", "CONTRACT_NAME", "PLAN_NAME", "FORMULARY_ID", "PREMIUM", "DEDUCTIBLE",
               "MA_REGION_CODE", "PDP_REGION_CODE", "STATE", "COUNTY_CODE", "SNP", "PLAN_SUPPRESSED_YN"]
PLANS = [
    ["S1111", "001", "000", "Alpha Health Plans", "Alpha Rx Basic (PDP)", "00026001", "45.10", "590.00", "", "4", "NJ", "", "0", "N"],
    ["S2222", "002", "000", "Beta Insurance", "Beta Rx Saver (PDP)", "00026002", "12.00", "590.00", "", "4", "NJ", "", "0", "N"],
    ["S3333", "001", "000", "Gamma Rx", "Gamma Rx Value (PDP)", "00026003", "5.00", "0", "", "5", "PA", "", "0", "N"],      # other region
    ["S4444", "001", "000", "Delta Rx", "Delta Rx Hidden (PDP)", "00026003", "1.00", "0", "", "4", "NJ", "", "0", "Y"],     # suppressed
    ["H1234", "001", "000", "Jersey Health", "Jersey Health Advantage (HMO)", "00026010", "0.00", "0.00", "3", "", "NJ", "31010", "0", "N"],
    ["H1234", "001", "000", "Jersey Health", "Jersey Health Advantage (HMO)", "00026010", "0.00", "0.00", "3", "", "NJ", "31020", "0", "N"],
    ["H5678", "002", "001", "Garden Care", "Garden Care Choice (PPO)", "00026010", "0.00", "0.00", "3", "", "NJ", "31010", "0", "N"],
    ["H9999", "003", "000", "Shore Dual", "Shore Dual Complete (HMO D-SNP)", "00026003", "0.00", "0.00", "3", "", "NJ", "31010", "2", "N"],  # SNP
    ["H7777", "001", "000", "Keystone MA", "Keystone MA (HMO)", "00026003", "0.00", "0.00", "5", "", "PA", "39010", "0", "N"],  # other state
    ["H8888", "001", "000", "Liberty MA", "Liberty MA (HMO)", "00026003", "0.00", "0.00", "5", "", "NJ", "39010", "0", "N"],  # NJ on paper, no NJ county
]
GEO_HEADER = ["COUNTY_CODE", "STATENAME", "COUNTY", "MA_REGION_CODE", "MA_REGION", "PDP_REGION_CODE", "PDP_REGION"]
GEO = [["31010", "New Jersey", "Atlantic", "03", "New Jersey", "04", "New Jersey"],
       ["31020", "New Jersey", "Bergen", "03", "New Jersey", "04", "New Jersey"],
       ["39010", "Pennsylvania", "Adams", "05", "Pennsylvania", "05", "Pennsylvania"]]
FORM_HEADER = ["FORMULARY_ID", "FORMULARY_VERSION", "CONTRACT_YEAR", "RXCUI", "NDC", "TIER_LEVEL_VALUE", "QUANTITY_LIMIT_YN",
               "QUANTITY_LIMIT_AMOUNT", "QUANTITY_LIMIT_DAYS", "PRIOR_AUTHORIZATION_YN", "STEP_THERAPY_YN", "SELECTED_DRUG_YN"]


def frow(fid, rxcui, tier, ql="N", amt="", days="", pa="N", st="N", ndc="00000000001"):
    return [fid, "1", "2026", rxcui, ndc, tier, ql, amt, days, pa, st, "N"]


FORM_PART1 = [
    frow("00026001", "314076", 1),                                   # lisinopril
    frow("00026001", "197885", 1),                                   # hydrochlorothiazide/lisinopril combination only
    frow("00026001", "562508", 2, ql="Y", amt="20.0", days="10"),    # amoxicillin/clavulanate
    frow("00026001", "1488574", 4, ndc="00000000002"),               # dapagliflozin [Farxiga], dearer duplicate row
    frow("00026001", "1488574", 3, pa="Y"),
    frow("00026001", "1545664", 3),                                  # empagliflozin [Jardiance]
    frow("00026001", "999001", 6),                                   # not a knowledge-base medicine; sets the tier count
    frow("00026002", "314076", 1),
    # S3333 (other region) and the SNP and PA plans cover everything: they must still lose
    *[frow("00026003", rx, 1) for rx in ("314076", "310798", "308191", "562508", "1488569", "1545664", "243670")],
]
FORM_PART2 = [
    frow("00026010", "314076", 1),
    frow("00026010", "1488574", 5, st="Y"),
    frow("00026010", "1545664", 3),
]
COST_HEADER = ["CONTRACT_ID", "PLAN_ID", "SEGMENT_ID", "COVERAGE_LEVEL", "TIER", "DAYS_SUPPLY", "TIER_SPECIALTY_YN", "DED_APPLIES_YN"]


def build_spuf(pharmacy_bytes=3_000_000):
    plan = _zip([("plan information  PPUF_2026Q1.txt", _psv(PLAN_HEADER, PLANS))])
    geo = _zip([("geographic locator file PPUF_2026Q1.txt", _psv(GEO_HEADER, GEO))])
    form = _zip([("basic drugs formulary file  PPUF_2026Q1 part 1.txt", _psv([h.lower() for h in FORM_HEADER], FORM_PART1)),
                 ("basic drugs formulary file  PPUF_2026Q1 part 2.txt", _psv(FORM_HEADER, FORM_PART2))])
    cost = _zip([("beneficiary cost file  PPUF_2026Q1.txt", _psv(COST_HEADER, [
        ["H1234", "1", "0", "1", "5", "1", "Y", "N"],
        ["H1234", "1", "0", "1", "1", "1", "N", "Y"],
        ["S1111", "001", "000", "1", "5", "1", "Y", "N"]]))])
    insulin = _zip([("insulin beneficiary cost file PPUF_2026Q1.txt", _psv(COST_HEADER, [["H1234", "001", "000", "1", "1", "1", "Y", "N"]]))])
    excluded = _zip([("excluded drugs formulary file  PPUF_2026Q1.txt",
                      _psv(["CONTRACT_ID", "PLAN_ID", "RXCUI", "TIER", "PRIOR_AUTH_YN"], [["S1111", "001", "243670", "1", "0"]]))])
    pharmacy = _zip([("pharmacy networks file PPUF_2026Q1 part 1.txt", random.Random(7).randbytes(pharmacy_bytes))], zipfile.ZIP_STORED)
    pricing = _zip([("pricing file PPUF_2026Q1.txt", _psv(["CONTRACT_ID", "PLAN_ID", "SEGMENT_ID", "NDC", "DAYS_SUPPLY", "UNIT_COST"], []))])
    outer = _zip([
        ("SPUF_2026_20260107/plan information  PPUF_2026Q1.zip", plan),
        ("SPUF_2026_20260107/pharmacy networks file PPUF_2026Q1 part 1.zip", pharmacy),
        ("SPUF_2026_20260107/geographic locator file PPUF_2026Q1.zip", geo),
        ("SPUF_2026_20260107/insulin beneficiary cost file PPUF_2026Q1.zip", insulin),
        ("SPUF_2026_20260107/beneficiary cost file  PPUF_2026Q1.zip", cost),
        ("SPUF_2026_20260107/excluded drugs formulary file  PPUF_2026Q1.zip", excluded),
        ("SPUF_2026_20260107/basic drugs formulary file  PPUF_2026Q1.zip", form),
        ("SPUF_2026_20260107/pricing file PPUF_2026Q1.zip", pricing),
    ], zipfile.ZIP_STORED)
    return outer, len(pharmacy)


# ------------------------------------------------------------------ mocked RxNav

RX = {"lisinopril": "29046", "hydrochlorothiazide": "5487", "amoxicillin": "723", "clavulanate": "48203",
      "dapagliflozin": "1488564", "empagliflozin": "1545653", "aspirin": "1191"}
NAMES = {v: k for k, v in RX.items()}
PRODUCTS = {
    "29046": [("314076", "lisinopril 10 MG Oral Tablet", "SCD"), ("104377", "lisinopril 10 MG Oral Tablet [Zestril]", "SBD"),
              ("197885", "hydrochlorothiazide 12.5 MG / lisinopril 10 MG Oral Tablet", "SCD")],
    "5487": [("310798", "hydrochlorothiazide 25 MG Oral Tablet", "SCD"),
             ("197885", "hydrochlorothiazide 12.5 MG / lisinopril 10 MG Oral Tablet", "SCD")],
    "723": [("308191", "amoxicillin 500 MG Oral Capsule", "SCD"),
            ("562508", "amoxicillin 875 MG / clavulanate potassium 125 MG Oral Tablet", "SCD"),
            ("617993", "amoxicillin 875 MG / clavulanate potassium 125 MG Oral Tablet [Augmentin]", "SBD")],
    "48203": [("562508", "amoxicillin 875 MG / clavulanate potassium 125 MG Oral Tablet", "SCD")],
    "1488564": [("1488569", "dapagliflozin 10 MG Oral Tablet", "SCD"), ("1488574", "dapagliflozin 10 MG Oral Tablet [Farxiga]", "SBD"),
                ("1486977", "dapagliflozin 10 MG / metformin hydrochloride 1000 MG 24HR Extended Release Oral Tablet", "SCD")],
    "1545653": [("1545664", "empagliflozin 10 MG Oral Tablet [Jardiance]", "SBD")],
    "1191": [("243670", "aspirin 81 MG Oral Tablet", "SCD")],
}


def rxnav_response(url: httpx.URL) -> httpx.Response:
    path, q = url.path, url.params
    if path.endswith("/rxcui.json"):
        rx = RX.get((q.get("name") or "").lower())
        return httpx.Response(200, json={"idGroup": {"name": q.get("name"), **({"rxnormId": [rx]} if rx else {})}})
    m = re.search(r"/rxcui/(\d+)/properties\.json$", path)
    if m:
        n = NAMES.get(m[1])
        return httpx.Response(200, json={"properties": {"rxcui": m[1], "name": n, "tty": "IN"}} if n else {})
    m = re.search(r"/rxcui/(\d+)/related\.json$", path)
    if m:
        ttys = (q.get("tty") or "").split()  # a space-separated list, as RxNav documents
        groups: dict = {}
        for rx, name, tty in PRODUCTS.get(m[1], []):
            if tty in ttys:
                groups.setdefault(tty, []).append({"rxcui": rx, "name": name, "tty": tty})
        return httpx.Response(200, json={"relatedGroup": {"rxcui": m[1], "termType": ttys, "conceptGroup": [
            {"tty": t, **({"conceptProperties": groups[t]} if t in groups else {})} for t in ttys]}})
    if path.endswith("/approximateTerm.json"):
        return httpx.Response(200, json={"approximateGroup": {"inputTerm": q.get("term"), "candidate": []}})
    return httpx.Response(404)


class FakeInternet:
    def __init__(self):
        self.zip, self.pharmacy_size = build_spuf()
        self.ranges = True
        self.catalog = True
        self.zip_bytes_served = 0
        self.urls: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.urls.append(str(url))
        if url.host == "catalog.data.gov":
            if not self.catalog:
                return httpx.Response(503)
            return httpx.Response(200, json={"success": True, "result": {"resources": [
                {"name": "SPUF_2025_20251015", "url": "https://data.cms.example/files/2025-10/x/SPUF_2025_20251015.zip"},
                {"name": "Record layout", "url": "https://data.cms.example/files/SPUFRecordLayout-2026.pdf"},
                {"name": "SPUF_2026_20260107", "url": ZIP_URL}]}})
        if url.path.endswith("SPUF_2026_20260107.zip"):
            rng = request.headers.get("range")
            if self.ranges and rng:
                m = re.match(r"bytes=(\d+)-(\d+)", rng)
                a, b = int(m[1]), min(int(m[2]), len(self.zip) - 1)
                body = self.zip[a:b + 1]
                self.zip_bytes_served += len(body)
                return httpx.Response(206, content=body, headers={"Content-Range": f"bytes {a}-{b}/{len(self.zip)}"})
            self.zip_bytes_served += len(self.zip)
            return httpx.Response(200, content=self.zip)
        if url.host == "rxnav.nlm.nih.gov":
            return rxnav_response(url)
        return httpx.Response(404)


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.delenv("DRUGS_OFFLINE", raising=False)
    monkeypatch.delenv("PARTD_ZIP_URL", raising=False)
    own = tmp_path / "partd"

    def data_dir():
        own.mkdir(parents=True, exist_ok=True)
        return own

    monkeypatch.setattr(partd, "data_dir", data_dir)  # keep the shared DATA_DIR free of a real-data subset
    monkeypatch.setattr(partd, "RXNAV_RATE", 0)
    monkeypatch.setattr(partd, "BLOCK_SIZE", 64 * 1024)
    monkeypatch.setattr(partd, "RETRY_DELAYS", (0, 0, 0))
    fake = FakeInternet()
    mock = httpx.MockTransport(fake.handle)
    monkeypatch.setattr(partd, "transport", mock)
    monkeypatch.setattr(net, "transport", mock)
    net.reset()
    net.clear_cache()
    partd.reset()
    yield fake
    net.reset()
    net.clear_cache()
    partd.reset()


def row(name, **kw):
    return {"id": f"m_{name}", "name": name, "dose": None, "unit": "mg", "frequency": None, "action": "start", **kw}


def formulary_source(result):
    return next(s for s in result["sources_checked"] if "formulary" in s["name"].lower())


# ------------------------------------------------------------------ unit pieces


def test_member_names_products_and_release_labels():
    assert partd.classify("SPUF_2026_20260107/plan information  PPUF_2026Q1.zip") == "plan"
    assert partd.classify("geographic locator file PPUF_2026Q1.zip") == "geo"
    assert partd.classify("basic drugs formulary file  PPUF_2026Q1.zip") == "formulary"
    assert partd.classify("beneficiary cost file  PPUF_2026Q1.zip") == "cost"
    for decoy in ("insulin beneficiary cost file PPUF_2026Q1.zip", "excluded drugs formulary file  PPUF_2026Q1.zip",
                  "pharmacy networks file PPUF_2026Q1 part 1.zip", "pricing file PPUF_2026Q1.zip",
                  "Indication Based Coverage Formulary File  PPUF_2026Q1.zip"):
        assert partd.classify(decoy) is None, decoy
    assert partd._components("lisinopril 10 MG Oral Tablet [Zestril]", "SBD") == ["lisinopril 10 MG Oral Tablet"]
    assert partd._components("hydrochlorothiazide 12.5 MG / lisinopril 10 MG Oral Tablet", "SCD") == [
        "hydrochlorothiazide 12.5 MG", "lisinopril 10 MG Oral Tablet"]
    pack = "{21 (ethinyl estradiol 0.035 MG / norethindrone 0.5 MG Oral Tablet) / 7 (inert ingredients 1 MG Oral Tablet) } Pack [Ortho-Novum]"
    assert partd._components(pack, "BPCK") == ["ethinyl estradiol 0.035 MG", "norethindrone 0.5 MG Oral Tablet"]
    assert partd.release_from_url(ZIP_URL) == ("2026-01-07", 2026)
    assert partd.release_label("2026-01-07", 2026) == "Q1 2026"
    assert partd.release_label("2025-10-15", 2025) == "Q4 2025"
    assert partd.release_text("2026-01-07", 2026) == "released 7 Jan 2026, contract year 2026"


# ------------------------------------------------------------------ the whole load


def test_build_subset_from_synthetic_spuf(web):
    t0 = time.monotonic()
    s = partd.run(generics=GENERICS)
    assert time.monotonic() - t0 < 20
    assert s["source_url"] == ZIP_URL and s["found_via"].startswith("data.gov catalog")
    assert s["release_label"] == "Q1 2026" and s["contract_year"] == 2026

    # plan choice: region, SNP and suppression filters, then coverage, counties and premium
    pdp, mapd = s["plans"]["PDP-A"], s["plans"]["MAPD-B"]
    assert (pdp["contract_id"], pdp["plan_id"], pdp["segment_id"]) == ("S1111", "001", "000")
    assert pdp["name"] == "Alpha Rx Basic (PDP)" and pdp["formulary_id"] == "00026001" and pdp["premium"] == 45.10
    assert pdp["why"].startswith("Covers 4 of 7 knowledge-base medicines, the most of the 2 PDP plans in New Jersey")
    assert (mapd["contract_id"], mapd["plan_id"]) == ("H1234", "001") and mapd["counties_served"] == 2
    assert "2 tied" in mapd["why"] and "serves 2 New Jersey counties" in mapd["why"]
    assert s["selection"]["pdp_candidates"] == 2 and s["selection"]["mapd_candidates"] == 2
    assert pdp["max_tier"] == 6 and mapd["specialty_tiers"] == [5] and pdp["specialty_tiers"] == [5]

    # RxNorm mapping: single ingredients never pick up combinations, combinations only combinations
    assert s["mapping"]["mapped"] == 7 and s["mapping"]["of"] == 7
    d = s["drugs"]["PDP-A"]
    assert d["lisinopril"]["covered"] and d["lisinopril"]["tier"] == 1 and d["lisinopril"]["best_rxcui"] == "314076"
    assert d["lisinopril"]["tier_text"] == "Tier 1 of 6" and d["lisinopril"]["generic_product"] is True
    assert d["hydrochlorothiazide"] == {"mapped": True, "covered": False}   # only the lisinopril combination is listed
    assert d["amoxicillin"] == {"mapped": True, "covered": False}           # amoxicillin/clavulanate does not count
    ac = d["amoxicillin-clavulanate"]
    assert ac["covered"] and ac["tier"] == 2 and ac["ql"] and ac["ql_detail"] == "20 per 10 days"
    dapa = d["dapagliflozin"]
    assert dapa["tier"] == 3 and dapa["pa"] and not dapa["st"] and dapa["generic_product"] is False  # duplicate tier-4 row ignored
    assert d["aspirin"] == {"mapped": True, "covered": False}               # excluded-drugs decoy not read
    m = s["drugs"]["MAPD-B"]
    assert m["dapagliflozin"]["tier"] == 5 and m["dapagliflozin"]["specialty"] and m["dapagliflozin"]["st"]
    assert m["dapagliflozin"]["tier_text"] == "Tier 5 of 5 (specialty)"
    assert m["lisinopril"]["specialty"] is False                             # insulin cost decoy not read

    # only what was needed was fetched: the 3 MB pharmacy member never was
    assert len(web.zip) > web.pharmacy_size > 2_500_000
    assert web.zip_bytes_served < 600_000
    assert partd.status()["state"] == "ready" and "Alpha Rx Basic (PDP) (PDP-A)" in partd.status()["detail"]
    assert partd.subset_path().exists() and not list(partd.data_dir().glob("work-*"))


def test_formulary_answers_from_the_real_subset(web):
    partd.run(generics=GENERICS)
    plans = formulary.plans()
    assert [p["plan_key"] for p in plans] == ["PDP-A", "MAPD-B", "COMMERCIAL"]
    assert plans[0]["name"] == "Alpha Rx Basic (PDP)" and plans[0]["data"] == "cms_puf"
    assert plans[0]["source"] == "Medicare Part D formulary: CMS public use file, Q1 2026 (Alpha Rx Basic (PDP))"
    assert "real-time benefit" in plans[2]["source"]

    look = formulary.lookup("PDP-A", "Farxiga")
    st = look["status"]
    assert look["data"] == "cms_puf" and look["loader"]["state"] == "ready"
    assert st["tier"] == 3 and st["tier_label"] == "Tier 3 of 6" and st["prior_auth"] and not st["step_therapy"]
    assert st["generic_available"] is False and "dapagliflozin 10 MG Oral Tablet [Farxiga]" in st["products"]
    assert look["alternatives"][0]["drug"] == "empagliflozin" and look["alternatives"][0]["tier"] == 3
    assert look["source"] == formulary.label("PDP-A") and "Sponsors have no influence" in look["ranking"]

    hctz = formulary.status("PDP-A", "hydrochlorothiazide")
    assert hctz["on_formulary"] is False and "No covered product" in hctz["note"]
    asa = formulary.status("PDP-A", "aspirin")
    assert asa["on_formulary"] is False and "Over-the-counter" in asa["note"]
    assert formulary.status("PDP-A", "warfarin")["on_formulary"] is None  # not in this subset's mapping
    assert formulary.restricted(formulary.status("MAPD-B", "dapagliflozin"))
    assert not formulary.restricted(formulary.status("PDP-A", "lisinopril"))
    assert not formulary.restricted(formulary.status("PDP-A", "empagliflozin"))  # tier 3, no PA/ST, not specialty
    assert "real-time benefit" in formulary.lookup("COMMERCIAL", "apixaban")["note"]


def test_check_visit_names_the_formulary_used_and_the_loader_state(web):
    patient = {"insurance": {"plan_key": "MAPD-B"}}
    before = formulary_source(check_visit([row("dapagliflozin")], patient))
    assert before["name"] == formulary.SAMPLE_LABEL and before["state"] == "sample" and before["data"] == "sample"

    partd.run(generics=GENERICS)
    r = check_visit([row("dapagliflozin")], patient)
    src = formulary_source(r)
    assert src["name"] == "Medicare Part D formulary: CMS public use file, Q1 2026 (Jersey Health Advantage (HMO))"
    assert src["state"] == "ready" and src["data"] == "cms_puf" and src["status"] == "ok"
    f = r["medications"][0]["formulary"]
    assert f["tier"] == 5 and f["specialty"] and f["step_therapy"] and f["data"] == "cms_puf"
    card = next(c for c in r["cards"] if c["category"] == "formulary")
    assert card["source"]["label"] == src["name"] and card["source"]["url"] == partd.LANDING_URL
    assert "step therapy" in card["summary"].lower()


def test_sample_fallback_while_loading_and_status_route(web):
    partd._set("loading", "Scanning the basic drugs formulary file…")
    plans = formulary.plans()
    assert plans[0]["name"].startswith("Sample Part D Plan A") and plans[0]["data"] == "sample"
    st = formulary.status("MAPD-B", "dapagliflozin")
    assert st["source"] == formulary.SAMPLE_LABEL and st["tier"] == 4  # the sample answer, clearly labelled

    src = formulary_source(check_visit([row("dapagliflozin")], {"insurance": {"plan_key": "MAPD-B"}}))
    assert src["state"] == "loading" and "still loading" in src["detail"] and src["name"] == formulary.SAMPLE_LABEL

    app = FastAPI()
    app.include_router(router, prefix="/api")

    @app.post("/login")
    def login(response: Response):
        auth.issue(response, "pharmacy")
        return {"ok": True}

    c = TestClient(app)
    assert c.post("/login").status_code == 200
    body = c.get("/api/formulary/plans").json()
    assert body["status"]["state"] == "loading" and body["status"]["source"] == "sample"
    assert "demo data" in body["label"]
    assert c.get("/api/drugs/sources").json()["formulary_status"]["state"] == "loading"

    partd.run(generics=GENERICS)
    body = c.get("/api/formulary/plans").json()
    assert body["status"]["state"] == "ready" and body["label"].startswith("Medicare Part D formulary: CMS public use file, Q1 2026")
    assert {p["plan_key"]: p["name"] for p in body["plans"]}["MAPD-B"] == "Jersey Health Advantage (HMO)"
    assert any("CMS public use file" in x["name"] for x in c.get("/api/drugs/sources").json()["sources"])


def test_catalog_down_and_no_range_support_uses_known_release_and_whole_download(web):
    web.catalog, web.ranges = False, False
    s = partd.run(generics=GENERICS)
    assert s["source_url"] == partd.FALLBACK_URL and s["found_via"] == "known release (catalog unavailable)"
    assert s["plans"]["PDP-A"]["contract_id"] == "S1111" and s["bytes_read"] == len(web.zip)


def test_discovery_order(web, monkeypatch):
    client = partd._client()
    assert partd.discover(client) == (ZIP_URL, "data.gov catalog (newest of 2 quarterly releases listed)")
    partd.reset()  # forget the cached answer
    web.catalog = False
    landing = ('<a href="/sites/default/files/2025-10/aa/SPUF_2025_20251015.zip">Q4 2025</a>'
               '{"downloadURL":"https:\\/\\/data.cms.gov\\/sites\\/default\\/files\\/2026-07\\/bb\\/SPUF_2026_20260709.zip"}')
    real_handle = web.handle

    def handle(request):
        if str(request.url) == partd.LANDING_URL:
            return httpx.Response(200, text=landing)
        return real_handle(request)

    monkeypatch.setattr(partd, "transport", httpx.MockTransport(handle))
    client = partd._client()
    assert partd.discover(client) == ("https://data.cms.gov/sites/default/files/2026-07/bb/SPUF_2026_20260709.zip",
                                      "CMS landing page (newest quarterly release)")
    monkeypatch.setenv("PARTD_ZIP_URL", "https://mirror.example/SPUF_2026_20260409.zip")
    assert partd.discover(client) == ("https://mirror.example/SPUF_2026_20260409.zip", "PARTD_ZIP_URL")


def test_fresh_subset_is_reused_and_stale_one_refreshed(web, monkeypatch):
    partd.run(generics=GENERICS)
    started = []

    class FakeThread:
        def __init__(self, *a, **k):
            started.append(k.get("name"))

        def start(self):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(partd.threading, "Thread", FakeThread)
    monkeypatch.setattr(partd, "_thread", None)
    monkeypatch.setattr(partd, "_autoload_off", lambda force: None)  # behave as in production, not as a test run
    calls = len(web.urls)
    partd.reset()
    partd.start_background()  # fresh subset: reused, no loader started, nothing fetched
    assert partd.status()["state"] == "ready" and started == [] and len(web.urls) == calls

    import json as _json
    p = partd.subset_path()
    data = _json.loads(p.read_text())
    data["built_at"] = "2025-01-01T00:00:00+00:00"  # older than 120 days
    p.write_text(_json.dumps(data))
    partd.reset()
    partd.start_background()
    assert started == ["partd-loader"]
    st = partd.status()
    assert st["state"] == "ready" and "refreshing" in st["detail"]  # the old subset is served while it refreshes
    assert formulary.plans()[0]["data"] == "cms_puf"


def test_offline_reports_unavailable_quickly(tmp_path, monkeypatch):
    monkeypatch.setenv("DRUGS_OFFLINE", "1")
    own = tmp_path / "partd"
    monkeypatch.setattr(partd, "data_dir", lambda: (own.mkdir(parents=True, exist_ok=True), own)[1])
    monkeypatch.setattr(partd, "_thread", None)
    partd.reset()
    t0 = time.monotonic()
    partd.start_background(force=True)
    assert partd.status()["state"] == "sample" and "DRUGS_OFFLINE" in partd.status()["detail"]
    assert partd._thread is None
    with pytest.raises(partd.PartDError):
        partd.run(generics=GENERICS)
    assert partd.status()["state"] == "failed" and "DRUGS_OFFLINE" in partd.status()["detail"]
    assert time.monotonic() - t0 < 1.0
    assert formulary.plans()[0]["data"] == "sample"
    partd.reset()


def test_no_network_fails_fast_and_keeps_the_sample(web, monkeypatch):
    def down(request):
        raise httpx.ConnectError("no route to host", request=request)

    mock = httpx.MockTransport(down)
    monkeypatch.setattr(partd, "transport", mock)
    monkeypatch.setattr(net, "transport", mock)
    t0 = time.monotonic()
    with pytest.raises(partd.PartDError):
        partd.run(generics=GENERICS)
    assert time.monotonic() - t0 < 5
    st = partd.status()
    assert st["state"] == "failed" and "Could not reach the CMS file server" in st["detail"] and "sample formulary" in st["detail"]
    assert formulary.loader_status()["source"] == "sample" and formulary.plans()[0]["data"] == "sample"



def test_pick_newest_prefers_the_year_in_effect():
    urls = ["https://x/SPUF_2024_20250430_2024Q3.zip", "https://x/SPUF_2025_20250410.zip",
            "https://x/SPUF_2026_20261008.zip", "https://x/SPUF_2026_20260709.zip", "https://x/SPUF_2027_20261204.zip"]
    from datetime import date as _date
    assert partd.pick_newest(urls, today=_date(2026, 12, 20)) == "https://x/SPUF_2026_20261008.zip"
    assert partd.pick_newest(urls, today=_date(2027, 1, 2)) == "https://x/SPUF_2027_20261204.zip"
    # a back-filled older quarter released later does not beat a newer contract year
    assert partd.pick_newest(urls[:2], today=_date(2026, 1, 1)) == "https://x/SPUF_2025_20250410.zip"
    assert partd.pick_newest(["https://x/readme.pdf"]) is None


def test_data_cms_catalog_lists_a_newer_release_than_data_gov(web, monkeypatch):
    dcat = ('{"dataset":[{"title":"Quarterly Prescription Drug Plan Formulary, Pharmacy Network, and Pricing Information",'
            '"distribution":[{"downloadURL":"https:\\/\\/data.cms.gov\\/sites\\/default\\/files\\/2026-10\\/cc\\/SPUF_2026_20261008.zip"},'
            '{"downloadURL":"https://data.cms.gov/sites/default/files/2026-07/bb/SPUF_2026_20260709.zip"}]}]}')
    real_handle = web.handle

    def handle(request):
        if str(request.url) == partd.DCAT_URL:
            return httpx.Response(200, text=dcat)
        return real_handle(request)

    monkeypatch.setattr(partd, "transport", httpx.MockTransport(handle))
    url, how = partd.discover(partd._client())
    assert url == "https://data.cms.gov/sites/default/files/2026-10/cc/SPUF_2026_20261008.zip"
    assert how == "data.cms.gov catalog (newest of 4 quarterly releases listed)"


def test_saved_subset_checks_weekly_for_a_newer_release(web, monkeypatch):
    partd.run(generics=GENERICS)  # built from the January 2026 file; the check is marked as done
    started = []

    class FakeThread:
        def __init__(self, *a, target=None, name=None, **k):
            started.append(name)
            self.target = target

        def start(self):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(partd.threading, "Thread", FakeThread)
    monkeypatch.setattr(partd, "_thread", None)
    monkeypatch.setattr(partd, "_autoload_off", lambda force: None)
    partd.reset()
    partd.start_background()
    assert started == []  # checked just now
    partd._check_marker().write_text('{"checked_at": "2026-01-01T00:00:00+00:00"}')
    partd.reset()
    partd.start_background()
    assert started == ["partd-check"]

    rebuilt = []
    monkeypatch.setattr(partd, "_loop", lambda: rebuilt.append(True))
    monkeypatch.setattr(partd, "discover", lambda client: ("https://x/SPUF_2026_20261008.zip", "data.cms.gov catalog"))
    partd._check_newer()
    assert rebuilt == [True]  # newer than the saved January subset: rebuild
    monkeypatch.setattr(partd, "discover", lambda client: (ZIP_URL, "data.gov catalog"))
    partd._check_newer()
    assert rebuilt == [True]  # same release: nothing to do
    assert not partd._check_due()
