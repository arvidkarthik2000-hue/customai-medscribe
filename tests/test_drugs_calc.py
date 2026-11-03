"""Calculators against hand-worked examples from the published equations."""
import pytest

from app.drugs import calculators as calc


@pytest.mark.parametrize("scr,age,sex,expected", [
    (1.0, 50, "F", 69),   # 142 * (1.0/0.7)^-1.2 * 0.9938^50 * 1.012 = 68.6
    (1.2, 60, "M", 69),   # 142 * (1.2/0.9)^-1.2 * 0.9938^60 = 69.2
    (1.0, 60, "M", 86),   # 142 * (1.0/0.9)^-1.2 * 0.9938^60 = 86.2
    (0.6, 30, "F", 124),  # 142 * (0.6/0.7)^-0.241 * 0.9938^30 * 1.012 = 123.8
])
def test_ckd_epi_2021(scr, age, sex, expected):
    assert round(calc.ckd_epi_2021(scr, age, sex)) == expected


def test_ckd_epi_rejects_bad_input():
    with pytest.raises(ValueError):
        calc.ckd_epi_2021(0, 50, "F")


def test_gfr_categories():
    assert calc.gfr_category(95).startswith("G1")
    assert calc.gfr_category(44).startswith("G3b")
    assert calc.gfr_category(14).startswith("G5")


def test_cha2ds2_vasc():
    r = calc.cha2ds2_vasc(hypertension=True, diabetes=True, age=76, sex="F")
    assert r["value"] == 5
    full = calc.cha2ds2_vasc(chf=True, hypertension=True, age=80, diabetes=True, stroke_tia=True, vascular=True, sex="F")
    assert full["value"] == 9
    assert calc.cha2ds2_vasc(age=40, sex="M")["value"] == 0


def test_has_bled():
    assert calc.has_bled(hypertension=True, elderly=True, drugs=True)["value"] == 3
    assert calc.has_bled(**{k: True for k in ("hypertension", "renal", "liver", "stroke", "bleeding", "labile_inr", "elderly", "drugs", "alcohol")})["value"] == 9


def test_bmi():
    assert round(calc.bmi(weight_kg=70, height_cm=175), 1) == 22.9
    assert round(calc.bmi(weight_lb=200, height_in=70), 1) == 28.7
    assert calc.bmi_category(31) == "Obesity"


def test_run_entry_point():
    out = calc.run("ckd_epi_2021", {"creatinine_mg_dl": 1.0, "age": 50, "sex": "F"})
    assert out["value"] == 69 and "Inker" in out["citation"]["label"]
    out2 = calc.run("cha2ds2_vasc", {"age": 72, "sex": "F", "hypertension": True})
    assert out2["value"] == 3 and "recommends anticoagulation" in out2["interpretation"]
    with pytest.raises(ValueError):
        calc.run("nope", {})
