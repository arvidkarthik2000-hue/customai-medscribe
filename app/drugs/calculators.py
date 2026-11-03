"""Clinical calculators coded from the primary papers (not copied from any app).

- CKD-EPI 2021 creatinine equation, race-free: Inker LA et al. N Engl J Med 2021;385:1737-49.
- CHA2DS2-VASc: Lip GYH et al. Chest 2010;137:263-72.
- HAS-BLED: Pisters R et al. Chest 2010;138:1093-100.
- BMI: weight (kg) / height (m)^2; WHO adult categories.
"""
from __future__ import annotations

CITATIONS = {
    "ckd_epi_2021": {"label": "Inker LA et al. New creatinine- and cystatin C-based equations to estimate GFR without race. N Engl J Med 2021;385:1737-49", "url": "https://doi.org/10.1056/NEJMoa2102953"},
    "cha2ds2_vasc": {"label": "Lip GYH et al. Refining clinical risk stratification for predicting stroke and thromboembolism in atrial fibrillation. Chest 2010;137:263-72", "url": "https://doi.org/10.1378/chest.09-1584"},
    "has_bled": {"label": "Pisters R et al. A novel user-friendly score (HAS-BLED) to assess 1-year risk of major bleeding in patients with atrial fibrillation. Chest 2010;138:1093-100", "url": "https://doi.org/10.1378/chest.10-0134"},
    "bmi": {"label": "World Health Organization: body mass index categories for adults", "url": "https://www.who.int/data/gho/data/themes/topics/topic-details/GHO/body-mass-index"},
}


def _is_female(sex) -> bool:
    return str(sex or "").strip().lower() in ("f", "female", "woman", "w")


def ckd_epi_2021(scr_mg_dl: float, age: float, sex) -> float:
    """eGFR in mL/min/1.73 m^2 (unrounded)."""
    scr = float(scr_mg_dl)
    age = float(age)
    if scr <= 0 or age < 18:
        raise ValueError("Creatinine must be positive and age at least 18.")
    female = _is_female(sex)
    kappa = 0.7 if female else 0.9
    alpha = -0.241 if female else -0.302
    ratio = scr / kappa
    egfr = 142 * (min(ratio, 1.0) ** alpha) * (max(ratio, 1.0) ** -1.200) * (0.9938 ** age)
    if female:
        egfr *= 1.012
    return egfr


def gfr_category(egfr: float) -> str:
    if egfr >= 90:
        return "G1 (normal or high)"
    if egfr >= 60:
        return "G2 (mildly decreased)"
    if egfr >= 45:
        return "G3a (mildly to moderately decreased)"
    if egfr >= 30:
        return "G3b (moderately to severely decreased)"
    if egfr >= 15:
        return "G4 (severely decreased)"
    return "G5 (kidney failure)"


def cha2ds2_vasc(*, chf=False, hypertension=False, age=0, diabetes=False, stroke_tia=False, vascular=False, sex="M") -> dict:
    age = float(age or 0)
    parts = []
    if chf:
        parts.append(("Congestive heart failure", 1))
    if hypertension:
        parts.append(("Hypertension", 1))
    if age >= 75:
        parts.append(("Age 75 or older", 2))
    elif age >= 65:
        parts.append(("Age 65–74", 1))
    if diabetes:
        parts.append(("Diabetes", 1))
    if stroke_tia:
        parts.append(("Prior stroke, TIA or thromboembolism", 2))
    if vascular:
        parts.append(("Vascular disease (prior MI, PAD or aortic plaque)", 1))
    if _is_female(sex):
        parts.append(("Female sex", 1))
    score = sum(p for _, p in parts)
    return {"value": score, "components": [{"item": n, "points": p} for n, p in parts]}


def has_bled(*, hypertension=False, renal=False, liver=False, stroke=False, bleeding=False, labile_inr=False,
             elderly=False, drugs=False, alcohol=False) -> dict:
    items = [("Hypertension (uncontrolled, systolic > 160)", hypertension), ("Abnormal renal function", renal),
             ("Abnormal liver function", liver), ("Stroke", stroke), ("Bleeding history or predisposition", bleeding),
             ("Labile INR", labile_inr), ("Elderly (over 65)", elderly), ("Antiplatelet or NSAID use", drugs),
             ("Alcohol (8 or more drinks a week)", alcohol)]
    parts = [{"item": n, "points": 1} for n, v in items if v]
    return {"value": len(parts), "components": parts}


def bmi(*, weight_kg=None, height_cm=None, weight_lb=None, height_in=None) -> float:
    if weight_kg is None and weight_lb is not None:
        weight_kg = float(weight_lb) * 0.45359237
    if height_cm is None and height_in is not None:
        height_cm = float(height_in) * 2.54
    if not weight_kg or not height_cm:
        raise ValueError("Weight and height are required.")
    m = float(height_cm) / 100.0
    return float(weight_kg) / (m * m)


def bmi_category(value: float) -> str:
    if value < 18.5:
        return "Underweight"
    if value < 25:
        return "Healthy weight"
    if value < 30:
        return "Overweight"
    return "Obesity"


def run(calculator: str, inputs: dict) -> dict:
    """Single entry point for POST /api/drugs/calc."""
    c = calculator
    if c == "ckd_epi_2021":
        egfr = ckd_epi_2021(inputs["creatinine_mg_dl"], inputs["age"], inputs.get("sex"))
        val = round(egfr)
        return {"calculator": c, "value": val, "unit": "mL/min/1.73 m²", "interpretation": f"eGFR category {gfr_category(egfr)}",
                "note": "Race-free CKD-EPI 2021 creatinine equation. Drug labels often dose by creatinine clearance (Cockcroft-Gault), which can differ.",
                "citation": CITATIONS[c]}
    if c == "cha2ds2_vasc":
        r = cha2ds2_vasc(chf=bool(inputs.get("chf")), hypertension=bool(inputs.get("hypertension")), age=inputs.get("age", 0),
                         diabetes=bool(inputs.get("diabetes")), stroke_tia=bool(inputs.get("stroke_tia")),
                         vascular=bool(inputs.get("vascular")), sex=inputs.get("sex", "M"))
        female = _is_female(inputs.get("sex"))
        threshold = 3 if female else 2
        interp = ("At or above the level where the 2023 ACC/AHA/ACCP/HRS atrial fibrillation guideline recommends anticoagulation"
                  if r["value"] >= threshold else "Below the guideline threshold for routine anticoagulation (2 for men, 3 for women); weigh individual factors")
        return {"calculator": c, **r, "unit": "points", "interpretation": interp, "citation": CITATIONS[c]}
    if c == "has_bled":
        r = has_bled(**{k: bool(inputs.get(k)) for k in ("hypertension", "renal", "liver", "stroke", "bleeding", "labile_inr", "elderly", "drugs", "alcohol")})
        interp = ("High bleeding risk (3 or more): address modifiable risk factors and review more often. A high score alone is not a reason to withhold anticoagulation."
                  if r["value"] >= 3 else "Low to moderate bleeding risk.")
        return {"calculator": c, **r, "unit": "points", "interpretation": interp, "citation": CITATIONS[c]}
    if c == "bmi":
        v = bmi(weight_kg=inputs.get("weight_kg"), height_cm=inputs.get("height_cm"), weight_lb=inputs.get("weight_lb"), height_in=inputs.get("height_in"))
        return {"calculator": c, "value": round(v, 1), "unit": "kg/m²", "interpretation": bmi_category(v), "citation": CITATIONS[c]}
    raise ValueError(f"Unknown calculator: {calculator}")
