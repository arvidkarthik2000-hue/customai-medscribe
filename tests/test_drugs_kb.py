"""Integrity checks on the curated knowledge base."""
from app.drugs.kb import kb


def test_onc_list_has_15_critical_rules():
    rules = [r for r in kb().interactions["rules"] if r.get("onc")]
    assert len(rules) == 15
    assert all(r["severity"] == "critical" for r in rules)


def test_every_rule_has_https_source_and_text():
    for r in kb().interactions["rules"]:
        assert r["sources"] and all(s["url"].startswith("https://") for s in r["sources"]), r["id"]
        assert r["effect"] and r["management"], r["id"]


def test_sound_alike_pairs_are_reciprocal():
    k = kb()
    for d in k.drugs.values():
        for sa in d.sound_alike:
            partner = k.get(sa["drug"])
            assert partner is not None
            assert any(p["drug"] == d.generic for p in partner.sound_alike), (d.generic, partner.generic)


def test_size_and_lookups():
    k = kb()
    assert len(k.drugs) >= 200
    assert k.lookup("Coumadin") == "warfarin"
    assert k.lookup("HCTZ") == "hydrochlorothiazide"
    assert k.lookup("losartan potassium 50 mg tablet") == "losartan"
    assert k.lookup("St. John's Wort") == "st johns wort"
    assert k.lookup("amoxicillin clavulanate") == "amoxicillin-clavulanate"


def test_combination_classes_include_ingredients():
    k = kb()
    assert "arb" in k.get("sacubitril-valsartan").classes
    assert "opioid" in k.get("hydrocodone-acetaminophen").classes


def test_no_safe_wording_in_kb():
    import json
    import re
    from app.drugs.kb import KB_DIR
    for p in KB_DIR.glob("*.json"):
        assert not re.search(r"\bsafe\b", p.read_text().lower()), p.name
