"""Generates app/drugs/kb/formulary_sample.json: a demo formulary laid out like
the CMS Part D Basic Drugs Formulary File (tier, PA, ST, QL). Plan names and
contract IDs are fictional; tiers are illustrative, not any real plan's."""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
DRUGS = json.loads((OUT / "drugs.json").read_text())["drugs"]

LABEL = "Sample formulary (structure of the CMS Part D files; demo data)"

OTC_ONLY = {"aspirin", "acetaminophen", "diphenhydramine", "chlorpheniramine", "doxylamine", "cetirizine", "loratadine",
            "fexofenadine", "dextromethorphan", "guaifenesin", "pseudoephedrine", "phenylephrine", "calcium carbonate",
            "ferrous sulfate", "magnesium oxide", "aluminum hydroxide-magnesium hydroxide", "loperamide",
            "polyethylene glycol 3350", "docusate", "senna", "cholecalciferol", "st johns wort",
            "acetaminophen-diphenhydramine", "ibuprofen-diphenhydramine"}
PART_B = {"irinotecan": "Given in a clinic or infusion center: covered under Part B, not Part D.",
          "heparin": "Usually given in a clinic or hospital: Part B or Part A, not Part D.",
          "ceftriaxone": "Injection given in the office: usually Part B.",
          "cefazolin": "Injection given in a clinic or hospital: usually Part B or A.",
          "piperacillin-tazobactam": "Infusion: usually Part A or B.",
          "methylene blue": "Hospital use: Part A or B.",
          "penicillin g benzathine": "Injection given in the office: usually Part B."}
NOT_LISTED = {"insulin detemir": "Discontinued in the US.", "vardenafil": "Part D excludes drugs used for erectile dysfunction.",
              "procarbazine": "Not on this sample formulary.", "thioridazine": "Not on this sample formulary.",
              "ergotamine": "Not on this sample formulary.", "rosiglitazone": "Not on this sample formulary.",
              "tranylcypromine": "Not on this sample formulary.", "isocarboxazid": "Not on this sample formulary."}

BRAND_ONLY = {"apixaban", "rivaroxaban", "edoxaban", "empagliflozin", "canagliflozin", "ertugliflozin", "linagliptin",
              "semaglutide", "dulaglutide", "tirzepatide", "exenatide", "insulin glargine", "insulin degludec",
              "insulin lispro", "insulin aspart", "insulin regular", "insulin nph", "finerenone", "umeclidinium-vilanterol",
              "fluticasone-vilanterol", "fluticasone-umeclidinium-vilanterol", "mometasone", "riociguat",
              "nirmatrelvir-ritonavir", "suvorexant", "tapentadol", "sacubitril-valsartan", "ticagrelor", "dapagliflozin",
              "sitagliptin", "tiotropium", "darunavir", "febuxostat"}

TIER1 = {"lisinopril", "enalapril", "ramipril", "benazepril", "losartan", "valsartan", "irbesartan", "olmesartan", "telmisartan",
         "amlodipine", "nifedipine", "diltiazem", "verapamil", "metoprolol", "atenolol", "carvedilol", "propranolol", "bisoprolol",
         "hydrochlorothiazide", "chlorthalidone", "furosemide", "torsemide", "bumetanide", "spironolactone",
         "triamterene-hydrochlorothiazide", "atorvastatin", "simvastatin", "rosuvastatin", "pravastatin", "lovastatin",
         "metformin", "glipizide", "glimepiride", "glyburide", "pioglitazone", "levothyroxine", "sertraline", "citalopram",
         "escitalopram", "fluoxetine", "paroxetine", "trazodone", "bupropion", "mirtazapine", "tamsulosin", "finasteride",
         "alendronate", "montelukast", "omeprazole", "pantoprazole", "famotidine", "warfarin", "clopidogrel", "allopurinol",
         "meloxicam", "prednisone", "amoxicillin", "cephalexin", "azithromycin", "doxycycline", "sulfamethoxazole-trimethoprim",
         "nitrofurantoin", "potassium chloride", "hydralazine", "clonidine", "isosorbide mononitrate", "ezetimibe", "folic acid",
         "cyanocobalamin"}

OPIOID_EDIT = "Opioid safety edits: 7-day supply limit for first fills in opioid-naive patients; pharmacist care-coordination alert at 90 MME/day or more (CMS Part D policy)."

# plan-specific overrides: generic -> dict(tier, pa, st, ql, ql_detail, note)
COMMON = {
    "apixaban": dict(tier=3, ql=True, ql_detail="60 tablets per 30 days"),
    "dabigatran": dict(tier=2),
    "edoxaban": dict(tier=4),
    "semaglutide": dict(tier=3, pa=True, ql=True, ql_detail="1 pen per 28 days", note="Covered for type 2 diabetes (and Wegovy for cardiovascular risk reduction); Part D excludes weight-loss-only use."),
    "tirzepatide": dict(tier=3, pa=True, ql=True, ql_detail="4 pens per 28 days", note="Covered for type 2 diabetes; Part D excludes weight-loss-only use."),
    "liraglutide": dict(tier=2, pa=True, note="Covered for type 2 diabetes; Part D excludes weight-loss-only use."),
    "exenatide": dict(tier=4, pa=True),
    "insulin glargine": dict(tier=3, note="Insulin cost-sharing is capped at $35 for a month's supply (Inflation Reduction Act)."),
    "insulin degludec": dict(tier=4, note="Insulin cost-sharing is capped at $35 for a month's supply (Inflation Reduction Act)."),
    "insulin regular": dict(tier=3, note="Insulin cost-sharing is capped at $35 for a month's supply (Inflation Reduction Act)."),
    "insulin nph": dict(tier=3, note="Insulin cost-sharing is capped at $35 for a month's supply (Inflation Reduction Act)."),
    "sacubitril-valsartan": dict(tier=3),
    "finerenone": dict(tier=4, pa=True),
    "riociguat": dict(tier=5, pa=True),
    "nirmatrelvir-ritonavir": dict(tier=3),
    "suvorexant": dict(tier=4, ql=True, ql_detail="30 tablets per 30 days"),
    "tapentadol": dict(tier=4, ql=True, ql_detail="Daily limit applies", note=OPIOID_EDIT),
    "fluticasone-umeclidinium-vilanterol": dict(tier=3, ql=True, ql_detail="1 inhaler per 30 days"),
    "mometasone": dict(tier=4),
    "darunavir": dict(tier=5),
    "febuxostat": dict(tier=2, st=True, note="Step therapy: try allopurinol first."),
    "zolpidem": dict(ql=True, ql_detail="30 tablets per 30 days"),
    "eszopiclone": dict(ql=True, ql_detail="30 tablets per 30 days"),
    "sumatriptan": dict(ql=True, ql_detail="9 tablets per 30 days"),
    "rizatriptan": dict(ql=True, ql_detail="12 tablets per 30 days"),
    "zolmitriptan": dict(ql=True, ql_detail="6 tablets per 30 days"),
    "eletriptan": dict(tier=3, ql=True, ql_detail="6 tablets per 30 days"),
    "tramadol": dict(ql=True, ql_detail="8 tablets (400 mg) per day", note=OPIOID_EDIT),
    "alprazolam": dict(ql=True, ql_detail="90 tablets per 30 days"),
    "lorazepam": dict(ql=True, ql_detail="90 tablets per 30 days"),
    "clonazepam": dict(ql=True, ql_detail="90 tablets per 30 days"),
    "diazepam": dict(ql=True, ql_detail="120 tablets per 30 days"),
    "ketorolac": dict(ql=True, ql_detail="20 tablets per 5 days"),
    "albuterol": dict(ql=True, ql_detail="2 inhalers per 30 days"),
    "testosterone": dict(pa=True),
    "sildenafil": dict(tier=2, pa=True, ql=True, ql_detail="90 tablets per 30 days", note="Covered only for pulmonary arterial hypertension (Revatio); Part D excludes drugs used for erectile dysfunction."),
    "tadalafil": dict(tier=2, pa=True, note="Covered for pulmonary arterial hypertension or BPH with prior authorization; Part D excludes drugs used for erectile dysfunction."),
    "esomeprazole": dict(tier=2, st=True, note="Step therapy: try omeprazole or pantoprazole first."),
    "dexlansoprazole": dict(tier=4, st=True, note="Step therapy: try omeprazole or pantoprazole first."),
    "lisdexamfetamine": dict(tier=2, ql=True, ql_detail="30 capsules per 30 days"),
    "methylphenidate": dict(ql=True, ql_detail="Daily limit applies"),
    "amphetamine-dextroamphetamine": dict(ql=True, ql_detail="Daily limit applies"),
    "buprenorphine-naloxone": dict(tier=2),
    "naloxone": dict(tier=1, note="Naloxone nasal spray is also sold OTC."),
}
for op in ["oxycodone", "oxycodone-acetaminophen", "hydrocodone-acetaminophen", "morphine", "hydromorphone", "methadone", "fentanyl", "codeine", "acetaminophen-codeine", "meperidine"]:
    COMMON[op] = dict(ql=True, ql_detail="Daily limit applies", note=OPIOID_EDIT)

PLAN_SPECIFIC = {
    "PDP-A": {
        "rivaroxaban": dict(tier=3, ql=True, ql_detail="60 tablets per 30 days"),
        "empagliflozin": dict(tier=3), "dapagliflozin": dict(tier=3), "canagliflozin": dict(tier=4), "ertugliflozin": dict(tier=4),
        "sitagliptin": dict(tier=3), "linagliptin": dict(tier=3),
        "dulaglutide": dict(tier=3, pa=True, ql=True, ql_detail="4 pens per 28 days", note="Covered for type 2 diabetes."),
        "insulin lispro": dict(tier=3, note="Insulin cost-sharing is capped at $35 for a month's supply (Inflation Reduction Act)."),
        "insulin aspart": dict(tier=4, note="Insulin cost-sharing is capped at $35 for a month's supply (Inflation Reduction Act)."),
        "ticagrelor": dict(tier=3),
        "tiotropium": dict(tier=3), "umeclidinium-vilanterol": dict(tier=3), "fluticasone-vilanterol": dict(tier=4),
        "budesonide-formoterol": dict(tier=3), "fluticasone-salmeterol": dict(tier=2),
        "celecoxib": dict(tier=2), "pregabalin": dict(tier=2), "duloxetine": dict(tier=2),
    },
    "MAPD-B": {
        "rivaroxaban": dict(tier=4, ql=True, ql_detail="60 tablets per 30 days"),
        "empagliflozin": dict(tier=3), "dapagliflozin": dict(tier=4, st=True, note="Step therapy: try metformin first."), "canagliflozin": dict(tier=4), "ertugliflozin": dict(tier=4),
        "sitagliptin": dict(tier=4, st=True, note="Step therapy: try metformin first."), "linagliptin": dict(tier=3),
        "dulaglutide": dict(tier=4, pa=True, ql=True, ql_detail="4 pens per 28 days", note="Covered for type 2 diabetes."),
        "insulin lispro": dict(tier=4, note="Insulin cost-sharing is capped at $35 for a month's supply (Inflation Reduction Act)."),
        "insulin aspart": dict(tier=3, note="Insulin cost-sharing is capped at $35 for a month's supply (Inflation Reduction Act)."),
        "ticagrelor": dict(tier=4),
        "tiotropium": dict(tier=3), "umeclidinium-vilanterol": dict(tier=4), "fluticasone-vilanterol": dict(tier=3),
        "budesonide-formoterol": dict(tier=2), "fluticasone-salmeterol": dict(tier=2),
        "celecoxib": dict(tier=2, st=True, note="Step therapy: try two generic NSAIDs first."),
        "pregabalin": dict(tier=2, st=True, note="Step therapy: try gabapentin first."),
        "duloxetine": dict(tier=2), "gabapentin": dict(tier=1), "cyclobenzaprine": dict(tier=1),
    },
}

GROUP_PRIORITY = ["doac", "vka", "heparin_class", "p2y12", "statin", "arni", "acei", "arb", "ccb_dhp", "ccb_nondhp", "beta_blocker", "thiazide",
                  "loop", "k_sparing", "sglt2", "dpp4", "glp1", "tzd", "sulfonylurea", "biguanide", "ssri", "snri", "tca", "z_drug",
                  "benzodiazepine", "ppi", "h2ra", "nsaid", "triptan", "opioid", "gabapentinoid", "muscle_relaxant", "penicillin",
                  "cephalosporin", "macrolide", "fluoroquinolone", "tetracycline_class", "leukotriene", "antipsychotic", "alpha_blocker",
                  "thyroid", "bisphosphonate", "pde5", "xanthine_oxidase_inhibitor", "first_gen_antihistamine", "second_gen_antihistamine",
                  "antiepileptic", "nitrate", "fibrate", "lipid_other", "antiviral", "azole_antifungal", "corticosteroid", "estrogen"]
GROUP_OVERRIDE = {
    "insulin glargine": "insulin_basal", "insulin degludec": "insulin_basal", "insulin detemir": "insulin_basal", "insulin nph": "insulin_basal",
    "insulin lispro": "insulin_bolus", "insulin aspart": "insulin_bolus", "insulin regular": "insulin_bolus",
    "fluticasone-salmeterol": "ics_laba", "budesonide-formoterol": "ics_laba", "fluticasone-vilanterol": "ics_laba",
    "umeclidinium-vilanterol": "lama_laba", "fluticasone-umeclidinium-vilanterol": "ics_lama_laba", "tiotropium": "lama",
    "ipratropium": "short_antimuscarinic", "ipratropium-albuterol": "saba_sama", "albuterol": "saba", "levalbuterol": "saba",
    "fluticasone": "ics", "budesonide": "ics", "mometasone": "ics",
    "acetaminophen": "analgesic", "oxycodone-acetaminophen": "opioid", "hydrocodone-acetaminophen": "opioid", "acetaminophen-codeine": "opioid",
}
GROUP_LABELS = {"insulin_basal": "Basal insulin", "insulin_bolus": "Mealtime insulin", "ics_laba": "ICS/LABA inhaler", "lama_laba": "LAMA/LABA inhaler",
                "ics_lama_laba": "ICS/LAMA/LABA inhaler", "lama": "LAMA inhaler", "saba": "Rescue inhaler", "ics": "Inhaled corticosteroid",
                "short_antimuscarinic": "Short-acting antimuscarinic", "saba_sama": "SABA/SAMA", "analgesic": "Analgesic"}


def group_of(generic, d):
    if generic in GROUP_OVERRIDE:
        return GROUP_OVERRIDE[generic]
    for g in GROUP_PRIORITY:
        if g in d["classes"]:
            return g
    return None


entries = {"PDP-A": {}, "MAPD-B": {}}
not_covered = {}
groups = {}
for generic, d in DRUGS.items():
    groups[generic] = group_of(generic, d)
    if generic in OTC_ONLY:
        not_covered[generic] = "Over-the-counter product: Part D excludes most non-prescription drugs."
        continue
    if generic in PART_B:
        not_covered[generic] = PART_B[generic]
        continue
    if generic in NOT_LISTED:
        not_covered[generic] = NOT_LISTED[generic]
        continue
    for plan in entries:
        base_tier = 3 if generic in BRAND_ONLY else (1 if generic in TIER1 else 2)
        e = {"tier": base_tier, "pa": False, "st": False, "ql": False, "ql_detail": None, "note": None,
             "generic_available": generic not in BRAND_ONLY}
        e.update(COMMON.get(generic, {}))
        e.update(PLAN_SPECIFIC[plan].get(generic, {}))
        entries[plan][generic] = e

out = {
    "label": LABEL,
    "year": 2026,
    "layout": "Mirrors the CMS Part D Basic Drugs Formulary File fields: TIER_LEVEL_VALUE, PRIOR_AUTHORIZATION_YN, STEP_THERAPY_YN, QUANTITY_LIMIT_YN (see the CMS public use file record layout).",
    "layout_url": "https://data.cms.gov/sites/default/files/2025-10/0564eb37-402d-4110-bd98-2d5399dc30e7/PUFRecordLayout-2026.pdf",
    "tiers": {"1": "Preferred generic", "2": "Generic", "3": "Preferred brand", "4": "Non-preferred drug", "5": "Specialty tier"},
    "plans": {
        "PDP-A": {"plan_key": "PDP-A", "name": "Sample Part D Plan A (standalone PDP)", "type": "PDP", "contract_id": "S0000", "plan_id": "001", "year": 2026, "source": LABEL},
        "MAPD-B": {"plan_key": "MAPD-B", "name": "Sample Medicare Advantage Plan B with drug coverage (MA-PD)", "type": "MA-PD", "contract_id": "H0000", "plan_id": "002", "year": 2026, "source": LABEL},
    },
    "commercial_note": "Commercial formulary not available in the demo; production uses real-time benefit checks through an e-prescribing partner.",
    "groups": groups,
    "group_labels": GROUP_LABELS,
    "entries": entries,
    "not_covered": not_covered,
}
(OUT / "formulary_sample.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
print(len(entries["PDP-A"]), "covered drugs per plan;", len(not_covered), "not covered")
