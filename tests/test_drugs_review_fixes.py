"""Offline tests for the clinical-review fixes: sign-gate context, label contraindications, DOAC label
criteria, patient-specific dose maximums, CrCl-based renal labels, allergy advice and prevention."""
import json
import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="ms-review-"))

import pytest

from app.clinical import fixtures
from app.drugs import doses, net, prevention
from app.drugs.engine import check_list, check_visit, requires_ack
from app.drugs.kb import kb


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("DRUGS_OFFLINE", "1")
    net.reset()
    yield
    net.reset()


def row(name, dose=None, unit="mg", frequency=None, action="start", **kw):
    r = {"id": kw.pop("id", f"m_{name}"), "name": name, "dose": dose, "unit": unit, "frequency": frequency, "action": action}
    r.update(kw)
    return r


def find(result, category, *meds):
    for c in result["cards"]:
        if c["category"] == category and all(m in c["meds"] for m in meds):
            return c
    return None


def run_sample(sample_id):
    s = fixtures.sample(sample_id)
    meds = json.loads(json.dumps(s["saved_note"]["medications"]))
    return check_visit(meds, dict(fixtures.patient(s["patient_id"])), use_network=False)


# ------------------------------------------------------------------ 1. sign gate and context

def test_visit_medicine_missing_from_chart_is_new_even_when_continued():
    r = check_visit([row("ibuprofen", "400", frequency="twice daily", action="continue")],
                    {"age": 68, "sex": "M", "medications": [{"name": "warfarin", "dose": "5", "unit": "mg"}]})
    c = find(r, "interaction", "warfarin", "ibuprofen")
    assert c["indicator"] == "warning" and c["context"] == "new" and c["requires_ack"]


def test_continued_chart_medicine_stays_existing():
    r = check_visit([row("ibuprofen", "400", frequency="twice daily", action="continue")],
                    {"age": 68, "sex": "M", "medications": [{"name": "warfarin"}, {"name": "ibuprofen"}]})
    c = find(r, "interaction", "warfarin", "ibuprofen")
    assert c["context"] == "existing" and not c["requires_ack"]


def test_requires_ack_rule():
    assert requires_ack({"indicator": "critical", "context": "existing", "category": "interaction"})
    assert requires_ack({"indicator": "warning", "context": "new", "category": "dose"})
    assert not requires_ack({"indicator": "warning", "context": "new", "category": "older_adult"})
    assert not requires_ack({"indicator": "warning", "context": "existing", "category": "interaction"})
    assert not requires_ack({"indicator": "info", "context": "new", "category": "renal"})


# ------------------------------------------------------------------ 2. potassium, label contraindications, conditions

def _k_patient(k_value):
    return {"age": 66, "sex": "M", "labs": [{"name": "Potassium", "value": k_value, "unit": "mmol/L", "date": "2026-10-20"}],
            "medications": [{"name": "lisinopril"}, {"name": "spironolactone"}]}


def test_hyperkalemia_combination_escalates_when_potassium_above_5():
    r = check_visit([row("potassium chloride", "20", unit="mEq", frequency="daily", action="continue")], _k_patient(5.1))
    c = find(r, "interaction", "lisinopril", "spironolactone", "potassium chloride")
    assert c["indicator"] == "critical" and c["detail"].startswith("Latest potassium 5.1 mmol/L, above 5.0.")
    assert c["potassium"] == 5.1 and c["requires_ack"]


def test_hyperkalemia_pair_not_escalated_at_normal_potassium():
    r = check_visit([row("trimethoprim-sulfamethoxazole", "800", frequency="twice daily")],
                    {"age": 60, "sex": "F", "labs": [{"name": "Potassium", "value": 4.4, "unit": "mmol/L"}],
                     "medications": [{"name": "lisinopril"}]})
    c = find(r, "interaction", "lisinopril")
    assert c and c["indicator"] == "warning"


@pytest.mark.parametrize("new, chart, severity", [
    ("clopidogrel", "omeprazole", "warning"),
    ("nirmatrelvir-ritonavir", "simvastatin", "critical"),
    ("nirmatrelvir-ritonavir", "amiodarone", "critical"),
    ("nirmatrelvir-ritonavir", "fluticasone-salmeterol", "warning"),
    ("nirmatrelvir-ritonavir", "quetiapine", "warning"),
    ("clarithromycin", "warfarin", "warning"),
    ("pimozide", "sertraline", "critical"),
    ("thioridazine", "fluoxetine", "critical"),
    ("cyclobenzaprine", "phenelzine", "critical"),
    ("cyclobenzaprine", "sertraline", "warning"),
    ("erythromycin", "fluconazole", "critical"),
    ("potassium chloride", "amiloride", "critical"),
    ("simvastatin", "fluconazole", "warning"),
    ("oxycodone", "clarithromycin", "warning"),
    ("tramadol", "diphenhydramine", "warning"),
    ("tramadol", "procarbazine", "critical"),
    ("ergotamine", "erythromycin", "critical"),
    ("tizanidine", "propafenone", "critical"),
])
def test_new_label_interactions(new, chart, severity):
    r = check_visit([row(new, None, frequency="daily")], {"age": 50, "sex": "M", "medications": [{"name": chart}]})
    hits = [c for c in r["cards"] if c["category"] in ("interaction", "qt") and new in c["meds"] and chart in c["meds"]]
    assert hits, (new, chart, [c["summary"] for c in r["cards"]])
    assert hits[0]["indicator"] == severity, hits[0]["summary"]


def test_clopidogrel_pantoprazole_does_not_alert():
    r = check_visit([row("clopidogrel", "75", frequency="daily")], {"age": 60, "medications": [{"name": "pantoprazole"}]})
    assert not find(r, "interaction", "clopidogrel", "pantoprazole")


def test_lovastatin_verapamil_within_label_limit_is_info():
    r = check_visit([row("verapamil", "120", frequency="daily")],
                    {"age": 60, "medications": [{"name": "lovastatin", "dose": "20", "unit": "mg", "frequency": "daily"}]})
    c = find(r, "interaction", "lovastatin", "verapamil")
    assert c and c["indicator"] == "info" and c["onc_high_priority"]
    r2 = check_visit([row("lovastatin", "40", frequency="daily")], {"age": 60, "medications": [{"name": "verapamil"}]})
    assert find(r2, "interaction", "lovastatin", "verapamil")["indicator"] == "warning"


def test_grades_from_the_review():
    arni = check_visit([row("sacubitril-valsartan", "49", frequency="twice daily")], {"age": 70, "medications": [{"name": "lisinopril"}]})
    acei = [c for c in arni["cards"] if "lisinopril" in c["meds"] and "sacubitril-valsartan" in c["meds"]]
    assert len(acei) == 1 and acei[0]["indicator"] == "critical"
    rules = {r["id"]: r for r in kb().interactions["rules"]}
    assert rules["anticoag-nsaid"]["severity"] == "warning" and rules["anticoag-nsaid"]["management"].startswith("Monitor closely")
    assert rules["trimethoprim-potassium"]["severity"] == "warning"
    assert rules["allopurinol-thiopurine"]["severity"] == "warning"


@pytest.mark.parametrize("drug, problem, icd, severity", [
    ("cyclobenzaprine", "Heart failure", "I50.22", "critical"),
    ("clarithromycin", "Coronary artery disease", "I25.10", "warning"),
    ("amiloride", "Type 2 diabetes with diabetic chronic kidney disease", "E11.22", "critical"),
])
def test_new_condition_rules(drug, problem, icd, severity):
    r = check_visit([row(drug, None, frequency="daily")], {"age": 60, "problems": [{"name": problem, "icd10": icd}]})
    c = find(r, "condition", drug)
    assert c and c["indicator"] == severity


def test_tramadol_hyponatremia_warning():
    r = check_visit([row("tramadol", "50", frequency="every 6 hours as needed")],
                    {"age": 82, "sex": "F", "labs": [{"name": "Sodium", "value": 134, "unit": "mmol/L"}],
                     "medications": [{"name": "sertraline"}]})
    c = next(c for c in r["cards"] if c["summary"].startswith("Tramadol and hyponatremia"))
    assert c["indicator"] == "warning" and "sodium 134" in c["summary"] and "woman aged 82" in c["summary"]
    assert "SSRI and SNRI labels" in c["detail"] and c["requires_ack"]
    r2 = check_visit([row("tramadol", "50", frequency="every 6 hours as needed")],
                     {"age": 50, "sex": "M", "labs": [{"name": "Sodium", "value": 140, "unit": "mmol/L"}]})
    assert not any(c["summary"].startswith("Tramadol and hyponatremia") for c in r2["cards"])


# ------------------------------------------------------------------ 3. DOAC label criteria and CrCl

AF = [{"name": "Atrial fibrillation", "icd10": "I48.91"}]


def _pt(age, sex, weight, scr, problems=AF):
    pt = {"age": age, "sex": sex, "problems": problems, "labs": [{"name": "Creatinine", "value": scr, "unit": "mg/dL"}]}
    if weight is not None:
        pt["vitals"] = {"weight_kg": weight}
    return pt


def test_apixaban_two_criteria_with_5mg_is_a_warning_with_the_patient_values():
    r = check_visit([row("apixaban", "5", frequency="twice daily")], _pt(84, "F", 57, 1.1))
    c = find(r, "dose", "apixaban")
    assert c["indicator"] == "warning" and "2 of 3" in c["summary"]
    assert "age 84" in c["detail"] and "weight 57 kg" in c["detail"] and "does not choose a dose" in c["detail"]


def test_apixaban_25mg_without_criteria_is_info_and_none_without_af():
    r = check_visit([row("apixaban", "2.5", frequency="twice daily")], _pt(70, "M", 90, 1.0))
    assert find(r, "dose", "apixaban")["indicator"] == "info"
    r2 = check_visit([row("apixaban", "5", frequency="twice daily")], _pt(84, "F", 57, 1.6, problems=[]))
    assert not find(r2, "dose", "apixaban")


def test_rivaroxaban_and_edoxaban_use_cockcroft_gault():
    r = check_visit([row("rivaroxaban", "20", frequency="daily")], _pt(80, "F", 55, 1.3))
    c = find(r, "dose", "rivaroxaban")
    assert c["indicator"] == "warning" and "Cockcroft-Gault with actual body weight 55 kg" in c["detail"]
    r2 = check_visit([row("edoxaban", "60", frequency="daily")], _pt(45, "M", 95, 0.8))
    c2 = find(r2, "dose", "edoxaban")
    assert c2["indicator"] == "critical" and "boxed" in c2["summary"].lower()
    r3 = check_visit([row("dabigatran", "150", frequency="twice daily")], _pt(70, "M", None, 1.0))
    assert find(r3, "dose", "dabigatran")["indicator"] == "info"


def test_nitrofurantoin_uses_crcl_not_egfr():
    # eGFR is above 60 for this patient, but creatinine clearance (small, older woman) is below 60
    pt = {"age": 82, "sex": "F", "vitals": {"weight_kg": 50}, "labs": [{"name": "Creatinine", "value": 0.8, "unit": "mg/dL"}]}
    r = check_visit([row("nitrofurantoin", "100", frequency="twice daily")], pt)
    assert r["patient_context"]["egfr"] >= 60 and r["patient_context"]["crcl"] < 60
    c = find(r, "renal", "nitrofurantoin")
    assert c["indicator"] == "critical" and "Creatinine clearance about" in c["detail"]


def test_crcl_label_falls_back_to_egfr_when_weight_missing():
    pt = {"age": 70, "sex": "M", "egfr": 40}
    r = check_visit([row("nitrofurantoin", "100", frequency="twice daily")], pt)
    c = find(r, "renal", "nitrofurantoin")
    assert c["indicator"] == "critical" and "eGFR was used" in c["detail"]


# ------------------------------------------------------------------ 4. age- and drug-specific maximums

@pytest.mark.parametrize("generic, dose, freq, age, co, status, phrase", [
    ("citalopram", "40", "daily", 70, set(), "above_max", "over age 60"),
    ("citalopram", "20", "daily", 70, set(), "ok", "within the label maximum for this patient"),
    ("citalopram", "40", "daily", 45, {"omeprazole"}, "above_max", "CYP2C19 inhibitor"),
    ("citalopram", "40", "daily", 45, set(), "ok", "within the label maximum."),
    ("zolpidem", "10", "at bedtime", 70, set(), "above_max", "older adults"),
    ("tramadol", "100", "every 6 hours", 80, set(), "above_max", "over age 75"),
    ("tramadol", "50", "every 6 hours", 80, set(), "ok", "over age 75: 300 mg/day"),
])
def test_patient_specific_dose_limits(generic, dose, freq, age, co, status, phrase):
    dc, card = doses.check(generic, dose, "mg", freq, age=age, co_generics=co)
    assert dc["status"] == status and phrase in dc["detail"], dc["detail"]
    assert (card is not None) == (status == "above_max")


def test_tablet_counts_and_bedtime_frequencies():
    assert doses.daily_mg("500", "mg", "up to 2 tablets three times daily as needed") == 3000
    assert doses.daily_mg("50", "mg", "1-2 tablets every 6 hours") == 400
    assert doses.daily_mg("50", "mg", "every 6 hours as needed, no more than 4 tablets a day") == 200
    for f in ("at night", "every night", "in the evening", "before bed"):
        assert doses.per_day(f) == 1, f
    dc, _ = doses.check("acetaminophen", "500", "mg", "2 tablets four times daily")
    assert dc["status"] == "ok" and "4,000 mg/day" in dc["detail"]


def test_dose_limit_card_reaches_the_visit():
    r = check_visit([row("citalopram", "40", frequency="daily")], {"age": 72, "sex": "F"})
    c = find(r, "dose", "citalopram")
    assert c["indicator"] == "warning" and "for this patient" in c["summary"] and c["requires_ack"]


# ------------------------------------------------------------------ 5. QT label set

def test_qt_label_set_excludes_postmarketing_only_drugs():
    rule = next(r for r in kb().interactions["rules"] if r["id"] == "onc-qt-high")
    listed = set(rule["all"][0]["drugs"])
    assert {"azithromycin", "ondansetron", "citalopram", "methadone"} <= listed
    assert not listed & {"escitalopram", "donepezil", "chlorpromazine"}
    r = check_visit([row("escitalopram", "10", frequency="daily")], {"age": 60, "medications": [{"name": "ondansetron"}]})
    c = find(r, "qt", "escitalopram", "ondansetron")
    assert c and c["indicator"] == "warning" and not c["onc_high_priority"]


# ------------------------------------------------------------------ 7. allergy advice

def test_penicillin_allergy_cephalexin_advice_and_other_cephalosporins():
    pt = {"age": 34, "sex": "F", "allergies": [{"substance": "Penicillin", "reaction": "hives"}]}
    r = check_visit([row("cephalexin", "500", frequency="twice daily")], pt)
    c = find(r, "allergy", "cephalexin")
    assert c["indicator"] == "warning" and c["management"].startswith("Ask what the reaction was")
    assert any("AAAAI" in s["label"] for s in c["sources"])
    r2 = check_visit([row("cefuroxime", "500", frequency="twice daily")], pt)
    c2 = find(r2, "allergy", "cefuroxime")
    assert c2 and c2["indicator"] == "info"


# ------------------------------------------------------------------ 6. prevention

def test_prevention_reminders_use_problems_medicines_and_smoking():
    smoker = {"current": True, "ever": True, "pack_years": 30}
    ids = {r["id"] for r in prevention.reminders(58, "M", smoking=smoker, bmi=31, problems=[{"name": "Hypertension", "icd10": "I10"}], medicines=[])}
    assert {"lung", "statin", "diabetes"} <= ids and "htn" not in ids
    ids2 = {r["id"] for r in prevention.reminders(58, "M", smoking=smoker, bmi=31, problems=[{"name": "Hypertension", "icd10": "I10"}],
                                                   medicines=["atorvastatin"])}
    assert "statin" not in ids2
    ids3 = {r["id"] for r in prevention.reminders(80, "F", done=["rsv"])}
    assert "rsv" not in ids3 and "falls" in ids3
    assert "htn" not in {r["id"] for r in prevention.reminders(50, "F")}  # needs the problem list


def test_prevention_card_merges_without_repeating_chart_gaps():
    r = run_sample("s1-knee-warfarin")
    prev = [c for c in r["cards"] if c["category"] == "prevention"]
    combined = [c for c in prev if c["summary"].startswith("Other preventive care to consider")]
    assert len(combined) == 1
    names = [i["name"] for i in combined[0]["items"]]
    assert "Pneumococcal vaccine" not in names and "Influenza vaccine" not in names and "Colorectal cancer screening" not in names
    assert "Abdominal aortic aneurysm screening" in names  # former smoker, man aged 65-75
    pl = check_list([row("warfarin", "5", frequency="daily")], {"age": 68, "sex": "M"})
    assert not any(c["category"] == "prevention" for c in pl["cards"])


# ------------------------------------------------------------------ 8. the sample visits

def _summary(r):
    return [(c["indicator"], c["category"], c["summary"]) for c in r["cards"]]


def test_sample_s1_cards():
    r = run_sample("s1-knee-warfarin")
    c = find(r, "interaction", "warfarin", "ibuprofen")
    assert c["indicator"] == "warning" and c["context"] == "new" and c["requires_ack"]
    assert not any(x["indicator"] == "critical" for x in r["cards"]), _summary(r)


def test_sample_s2_cards():
    r = run_sample("s2-diabetes-ckd")
    assert find(r, "renal", "metformin")["indicator"] == "critical"
    sglt2 = find(r, "renal", "empagliflozin")
    assert sglt2["indicator"] == "info" and "glycemic control is not recommended at eGFR below 30" in sglt2["detail"]
    assert "contraindicated" not in sglt2["detail"].lower()


def test_sample_s3_cards():
    r = run_sample("s3-strep-penicillin")
    c = find(r, "allergy", "cephalexin")
    assert c["indicator"] == "warning" and "Ask what the reaction was" in c["detail"]


def test_sample_s4_cards():
    r = run_sample("s4-pneumonia-hf")
    assert find(r, "interaction", "simvastatin", "clarithromycin")["indicator"] == "critical"
    k = find(r, "interaction", "lisinopril", "spironolactone", "potassium chloride")
    assert k["indicator"] == "critical" and "5.1" in k["detail"] and k["requires_ack"]
    assert find(r, "condition", "clarithromycin")["indicator"] == "warning"


def test_sample_s5_cards():
    r = run_sample("s5-insomnia-back-pain")
    assert find(r, "interaction", "sertraline", "tramadol")["indicator"] == "warning"
    assert find(r, "interaction", "tramadol", "diphenhydramine")["indicator"] == "warning"
    assert find(r, "older_adult", "diphenhydramine")["indicator"] == "warning"
    assert any(c["summary"].startswith("Tramadol and hyponatremia") for c in r["cards"])
    tram = next(m for m in r["medications"] if m.get("name") == "tramadol")
    assert "over age 75" in tram["dose_check"]["detail"]


def test_sample_notes_use_generic_names_and_symptom_codes():
    s5 = fixtures.sample("s5-insomnia-back-pain")["saved_note"]["patient_instructions"]
    assert "Tylenol" not in s5 and "Benadryl" not in s5
    s4 = fixtures.sample("s4-pneumonia-hf")["saved_note"]
    assert {d["icd10"] for d in s4["diagnoses"]} == {"R05.1", "R50.9", "I50.22"}
    assert "Suspected" in s4["visit_summary"]


def test_pharmacy_check_takes_weight_potassium_and_sodium():
    r = check_list([row("apixaban", "5", frequency="twice daily")],
                   {"age": 84, "sex": "F", "weight_kg": 57, "creatinine": 1.1, "problems": ["Atrial fibrillation"]})
    assert r["patient_context"]["crcl"] is not None
    assert find(r, "dose", "apixaban")["indicator"] == "warning"
    r2 = check_list([row("potassium chloride", "20", unit="mEq", frequency="daily")],
                    {"age": 70, "potassium": 5.4, "problems": []})
    assert r2["patient_context"]["potassium"] == 5.4
