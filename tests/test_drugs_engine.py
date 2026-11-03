"""Offline tests for the medication-safety engine (curated KB only)."""
import json
import os
import re
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-drugs-"))

import pytest

from app.drugs import net
from app.drugs.engine import brand_and_generic_terms, check_list, check_visit, strip_added_brands


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("DRUGS_OFFLINE", "1")
    net.reset()
    yield
    net.reset()


def cards(result, category=None):
    return [c for c in result["cards"] if category is None or c["category"] == category]


def find(result, category, *meds):
    for c in cards(result, category):
        if all(m in c["meds"] for m in meds):
            return c
    return None


def row(name, dose=None, unit="mg", frequency=None, action="start", **kw):
    r = {"id": kw.pop("id", f"m_{name}"), "name": name, "dose": dose, "unit": unit, "frequency": frequency, "action": action}
    r.update(kw)
    return r


# ------------------------------------------------------------------ sample-visit scenarios

def test_warfarin_plus_new_ibuprofen():
    r = check_visit([row("ibuprofen", "600", frequency="three times daily")],
                    {"age": 71, "sex": "F", "medications": [{"name": "warfarin", "dose": "5", "unit": "mg"}],
                     "problems": [{"name": "Atrial fibrillation", "icd10": "I48.91"}, {"name": "Hypertension", "icd10": "I10"}]})
    c = find(r, "interaction", "warfarin", "ibuprofen")
    assert c and c["indicator"] == "warning" and c["context"] == "new"
    assert "acetaminophen" in c["detail"].lower()
    assert c["source"]["url"].startswith("https://dailymed.nlm.nih.gov/")
    assert c["grading"].startswith("Demo grading")
    calc = cards(r, "calculator")
    assert calc and calc[0]["value"] == 3  # age 65-74 (1) + female (1) + hypertension (1)
    assert r["medications"][0]["match"]["status"] == "matched"


def test_metformin_egfr_28_is_critical_renal():
    r = check_visit([row("metformin", "1000", frequency="twice daily", action="continue")],
                    {"age": 68, "sex": "M", "egfr": 28, "problems": [{"name": "Type 2 diabetes", "icd10": "E11.9"}]})
    c = find(r, "renal", "metformin")
    assert c and c["indicator"] == "critical"
    assert "contraindicated" in c["detail"].lower() and "does not calculate doses" in c["detail"]


def test_metformin_egfr_38_is_warning():
    r = check_visit([row("metformin", "500", frequency="daily")], {"age": 60, "sex": "F", "egfr": 38})
    c = find(r, "renal", "metformin")
    assert c and c["indicator"] == "warning"


def test_egfr_computed_from_creatinine():
    r = check_visit([row("metformin", "500", frequency="daily")],
                    {"age": 70, "sex": "F", "labs": [{"name": "Creatinine", "value": 2.0, "unit": "mg/dL", "date": "2026-10-01"}]})
    assert r["patient_context"]["egfr"] == 26
    c = find(r, "renal", "metformin")
    assert c["indicator"] == "critical" and "CKD-EPI 2021" in c["detail"]


def test_penicillin_allergy_amoxicillin_and_cephalexin():
    pt = {"age": 34, "sex": "F", "allergies": [{"substance": "Penicillin", "reaction": "hives"}]}
    r = check_visit([row("amoxicillin", "875", frequency="twice daily")], pt)
    c = find(r, "allergy", "amoxicillin")
    assert c and c["indicator"] == "critical" and "hives" in c["summary"]
    r2 = check_visit([row("cephalexin", "500", frequency="four times daily")], pt)
    c2 = find(r2, "allergy", "cephalexin")
    assert c2 and c2["indicator"] == "warning" and "side chain" in c2["detail"]


def test_discussed_medicine_does_not_alert():
    pt = {"age": 34, "sex": "F", "allergies": ["penicillin"]}
    r = check_visit([row("amoxicillin", "875", frequency="twice daily", action="discussed")], pt)
    assert not cards(r, "allergy")


def test_simvastatin_clarithromycin_is_onc_critical():
    r = check_visit([row("clarithromycin", "500", frequency="twice daily")],
                    {"age": 59, "sex": "M", "medications": [{"name": "simvastatin", "dose": "40", "unit": "mg"}]})
    c = find(r, "interaction", "simvastatin", "clarithromycin")
    assert c and c["indicator"] == "critical" and c["onc_high_priority"]
    assert any("PMC3422823" in (s["url"] or "") for s in c["sources"])


def test_sertraline_tramadol_serotonin():
    r = check_visit([row("tramadol", "50", frequency="every 6 hours as needed")],
                    {"age": 52, "sex": "F", "medications": [{"name": "sertraline", "dose": "100", "unit": "mg"}]})
    c = find(r, "interaction", "sertraline", "tramadol")
    assert c and c["indicator"] == "warning" and "serotonin" in c["detail"].lower()


def test_diphenhydramine_in_82_year_old():
    r = check_visit([row("diphenhydramine", "50", frequency="at bedtime", indication="sleep")],
                    {"age": 82, "sex": "F", "insurance": {"plan_key": "PDP-A"}})
    c = find(r, "older_adult", "diphenhydramine")
    assert c and c["indicator"] == "warning" and "STOPP" in c["source"]["label"]
    assert r["medications"][0]["formulary"]["on_formulary"] is False  # OTC: excluded from Part D


def test_hydroxyzine_hydralazine_sound_alike_guard():
    r = check_visit([row("hydroxyzine", "25", frequency="three times daily", action="continue", indication="blood pressure")],
                    {"age": 66, "sex": "M", "medications": [{"name": "hydralazine", "dose": "25", "unit": "mg"}],
                     "problems": [{"name": "Hypertension", "icd10": "I10"}]})
    m = r["medications"][0]["match"]
    assert m["status"] == "ambiguous"
    assert "hydralazine" in m["sound_alike"]
    assert {c["name"] for c in m["candidates"]} >= {"hydroxyzine", "hydralazine"}


def test_hydroxyzine_for_itch_is_matched_not_ambiguous():
    r = check_visit([row("hydroxyzine", "25", frequency="at bedtime", indication="itching")], {"age": 40, "sex": "F"})
    m = r["medications"][0]["match"]
    assert m["status"] == "matched" and m["sound_alike"] == ["hydralazine"]


def test_lisinopril_spironolactone_potassium_single_merged_card():
    r = check_visit([row("spironolactone", "25", frequency="daily")],
                    {"age": 74, "sex": "M", "medications": [{"name": "lisinopril", "dose": "20", "unit": "mg"},
                                                            {"name": "potassium chloride", "dose": "20", "unit": "mEq"}]})
    hk = [c for c in r["cards"] if set(c["meds"]) >= {"lisinopril", "spironolactone", "potassium chloride"}]
    assert len(hk) == 1 and hk[0]["indicator"] == "critical"
    assert "Also:" in hk[0]["detail"]


# ------------------------------------------------------------------ other rules

def test_qt_pair_and_high_risk_qt():
    r = check_visit([row("ondansetron", "4", frequency="every 8 hours")], {"medications": [{"name": "azithromycin"}]})
    c = find(r, "qt", "ondansetron", "azithromycin")
    assert c and c["indicator"] == "warning"
    r2 = check_visit([row("methadone", "10", frequency="twice daily")], {"medications": [{"name": "amiodarone"}]})
    c2 = find(r2, "qt", "methadone", "amiodarone")
    assert c2 and c2["indicator"] == "critical" and c2["onc_high_priority"]


def test_opioid_benzodiazepine_critical():
    r = check_visit([row("oxycodone", "5", frequency="every 6 hours as needed")], {"medications": [{"name": "alprazolam"}]})
    assert find(r, "interaction", "oxycodone", "alprazolam")["indicator"] == "critical"


def test_methotrexate_daily_dosing_flag():
    r = check_visit([row("methotrexate", "7.5", frequency="daily")], {})
    c = find(r, "dose", "methotrexate")
    assert c and c["indicator"] == "critical"
    weekly = check_visit([row("methotrexate", "15", frequency="once weekly")], {})
    assert not cards(weekly, "dose")


def test_dose_above_label_maximum():
    r = check_visit([row("ibuprofen", "1200", frequency="three times daily")], {})
    c = find(r, "dose", "ibuprofen")
    assert c and "3,200" in c["detail"]
    assert r["medications"][0]["dose_check"]["status"] == "above_max"
    ok = check_visit([row("lisinopril", "20", frequency="once daily")], {})
    assert ok["medications"][0]["dose_check"]["status"] == "ok"


def test_simvastatin_dose_limit_downgrade():
    hi = check_visit([row("amlodipine", "5", frequency="daily")], {"medications": [{"name": "simvastatin", "dose": "40", "unit": "mg", "frequency": "daily"}]})
    assert find(hi, "interaction", "simvastatin", "amlodipine")["indicator"] == "warning"
    lo = check_visit([row("amlodipine", "5", frequency="daily"), row("simvastatin", "20", frequency="daily", action="continue")], {})
    assert find(lo, "interaction", "simvastatin", "amlodipine")["indicator"] == "info"


def test_duplicate_ingredient_acetaminophen():
    r = check_visit([row("hydrocodone-acetaminophen", "1", unit="tablet", frequency="every 6 hours as needed")],
                    {"medications": [{"name": "Tylenol"}]})
    c = find(r, "duplicate", "hydrocodone-acetaminophen", "acetaminophen")
    assert c and "acetaminophen" in c["summary"]


def test_duplicate_class_nsaids_and_ketorolac_critical():
    r = check_visit([row("meloxicam", "15", frequency="daily")], {"medications": [{"name": "naproxen"}]})
    assert find(r, "duplicate", "meloxicam", "naproxen")
    r2 = check_visit([row("ketorolac", "10", frequency="every 6 hours")], {"medications": [{"name": "ibuprofen"}]})
    assert find(r2, "interaction", "ketorolac", "ibuprofen")["indicator"] == "critical"
    assert not find(r2, "duplicate", "ketorolac", "ibuprofen")


def test_sulfa_allergy_and_celecoxib_label_contraindication():
    r = check_visit([row("celecoxib", "200", frequency="daily")], {"allergies": [{"substance": "Sulfa", "reaction": "rash"}]})
    c = find(r, "allergy", "celecoxib")
    assert c and c["indicator"] == "critical"


def test_condition_rules():
    r = check_visit([row("propranolol", "40", frequency="twice daily")], {"problems": [{"name": "Asthma", "icd10": "J45.909"}]})
    assert find(r, "condition", "propranolol")["indicator"] == "critical"
    r2 = check_visit([row("pioglitazone", "30", frequency="daily")], {"problems": [{"name": "Chronic systolic heart failure", "icd10": "I50.22"}]})
    assert find(r2, "condition", "pioglitazone")["indicator"] == "critical"
    r3 = check_visit([row("naproxen", "500", frequency="twice daily")], {"egfr": 41})
    assert find(r3, "condition", "naproxen")


def test_stopped_medicine_creates_no_interaction():
    r = check_visit([row("ibuprofen", action="stop"), row("apixaban", "5", frequency="twice daily")], {})
    assert not find(r, "interaction", "apixaban", "ibuprofen")


def test_chart_only_pairs_are_not_re_alerted():
    r = check_visit([row("lisinopril", "10", frequency="daily", action="continue")],
                    {"medications": [{"name": "warfarin"}, {"name": "ibuprofen"}]})
    assert not find(r, "interaction", "warfarin", "ibuprofen")


def test_formulary_status_and_card():
    r = check_visit([row("semaglutide", "0.25", frequency="weekly")], {"insurance": {"plan_key": "PDP-A"}})
    f = r["medications"][0]["formulary"]
    assert f["tier"] == 3 and f["prior_auth"] is True
    c = find(r, "formulary", "semaglutide")
    assert c and "Sponsors have no influence" in c["detail"]
    r2 = check_visit([row("apixaban")], {"insurance": {"plan_key": "COMMERCIAL"}})
    assert r2["medications"][0]["formulary"]["available"] is False


def test_empty_result_message_and_no_safe_wording():
    r = check_list(["amlodipine", "atorvastatin"], {"age": 50, "sex": "M"})
    assert r["empty_message"] == "No interaction found in these sources."
    text = json.dumps(r).lower()
    assert not re.search(r"\bsafe\b", text)
    names = [s["name"] for s in r["sources_checked"]]
    assert any("Curated" in n for n in names)
    assert all(s["status"] in ("unavailable", "not_used") for s in r["sources_checked"] if "RxNorm" in s["name"])


def test_card_ids_are_stable():
    pt = {"medications": [{"name": "warfarin"}]}
    a = check_visit([row("ibuprofen", "400", frequency="three times daily")], pt)
    b = check_visit([row("ibuprofen", "400", frequency="three times daily")], pt)
    assert [c["id"] for c in a["cards"]] == [c["id"] for c in b["cards"]]


def test_brand_and_misspelled_names_match_locally():
    r = check_visit([row("Eliquis"), row("Lipitor"), row("metoprolol succinate ER 50 mg tablet"), row("hydroxizine")], {})
    by = {x["name"]: x["match"] for x in r["medications"]}
    assert by["Eliquis"]["ingredient"] == "apixaban"
    assert by["Lipitor"]["ingredient"] == "atorvastatin"
    assert by["metoprolol succinate ER 50 mg tablet"]["ingredient"] == "metoprolol"
    assert by["hydroxizine"]["ingredient"] == "hydroxyzine"
    assert r["medications"][0]["high_risk"] is True and r["medications"][0]["high_risk_reason"] == "anticoagulant"


def test_unknown_name_offline_is_unmatched():
    r = check_visit([row("zzqx totally unknown")], {})
    assert r["medications"][0]["match"]["status"] == "unmatched"


def test_contract_shape():
    r = check_visit([row("ibuprofen", "400", frequency="three times daily")], {"medications": [{"name": "warfarin"}]})
    assert set(r) >= {"medications", "cards", "sources_checked", "checked_at"}
    m = r["medications"][0]
    for k in ("match", "high_risk", "high_risk_reason", "schedule", "formulary", "dose_check", "confirmed", "dose_confirmed"):
        assert k in m
    assert set(m["match"]) >= {"status", "rxcui", "ingredient", "display", "candidates", "sound_alike", "source"}
    for c in r["cards"]:
        assert set(c) >= {"id", "indicator", "category", "summary", "detail", "meds", "source", "override_reasons"}
        assert c["indicator"] in ("critical", "warning", "info")


# ------------------------------------------------------------------ helpers

def test_strip_added_brands():
    text, removed = strip_added_brands("Start Lispro (Humalog) 4 units and metformin (Glucophage) 500 mg; knee (left) pain (PRN).")
    assert text == "Start Lispro 4 units and metformin 500 mg; knee (left) pain (PRN)."
    assert removed == ["Humalog", "Glucophage"]
    # a brand that does not belong to the preceding generic is left alone
    text2, removed2 = strip_added_brands("She takes aspirin (Eliquis) daily.")
    assert removed2 == [] and "(Eliquis)" in text2


def test_brand_and_generic_terms():
    terms = brand_and_generic_terms("cardiology")
    assert "Warfarin" in terms[:40] and "Eliquis" in terms
    assert terms.index("Apixaban") < terms.index("Eliquis")
    assert len(set(t.lower() for t in terms)) == len(terms)
    assert brand_and_generic_terms("unknown_specialty")  # falls back to family medicine


def test_intolerance_is_downgraded():
    r = check_visit([row("tramadol", "50", frequency="every 6 hours as needed")],
                    {"allergies": [{"substance": "codeine", "reaction": "nausea and vomiting (intolerance)"}]})
    c = cards(r, "allergy")[0]
    assert c["indicator"] == "info" and c["summary"].startswith("Intolerance to codeine")
    r2 = check_visit([row("tramadol", "50", frequency="every 6 hours as needed")],
                     {"allergies": [{"substance": "codeine", "reaction": "hives"}]})
    assert cards(r2, "allergy")[0]["indicator"] == "warning"


def test_otc_formulary_card_suppressed_but_row_status_kept():
    r = check_visit([row("acetaminophen", "500", frequency="every 6 hours as needed")], {"insurance": {"plan_key": "PDP-A"}})
    assert not cards(r, "formulary")
    assert r["medications"][0]["formulary"]["on_formulary"] is False


def test_formulary_alternatives_are_screened_for_the_patient():
    r = check_visit([row("celecoxib", "200", frequency="daily")], {"insurance": {"plan_key": "MAPD-B"}, "medications": [{"name": "warfarin"}]})
    f = next(c for c in cards(r, "formulary"))
    assert f["alternatives"] == []  # every NSAID alternative would interact with warfarin
    r2 = check_visit([row("dapagliflozin", "10", frequency="daily")], {"age": 60, "insurance": {"plan_key": "MAPD-B"}})
    alts = next(c for c in cards(r2, "formulary"))["alternatives"]
    assert alts and alts[0]["drug"] == "empagliflozin"


def test_lasa_pair_on_same_list():
    r = check_visit([row("hydralazine", "25", frequency="three times daily", action="continue", indication="blood pressure"),
                     row("hydroxyzine", "25", frequency="at bedtime", action="continue", indication="itching")],
                    {"medications": [{"name": "hydralazine"}, {"name": "hydroxyzine hydrochloride"}]})
    c = cards(r, "name_check")
    assert len(c) == 1 and set(c[0]["meds"]) == {"hydralazine", "hydroxyzine"}
    assert all(m["match"]["status"] == "matched" for m in r["medications"])
    assert "Look-alike" in r["medications"][0]["match"]["reason"]


def test_oral_contraceptive_matches():
    r = check_visit([row("norgestimate and ethinyl estradiol", action="continue")], {"problems": [{"name": "History of DVT", "icd10": "Z86.718"}]})
    assert r["medications"][0]["match"]["ingredient"] == "norgestimate-ethinyl estradiol"
    assert cards(r, "condition")[0]["indicator"] == "critical"
