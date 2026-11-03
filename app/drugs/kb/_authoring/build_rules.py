"""Generates the rule files in app/drugs/kb/ and validates every reference."""
import json
import sys
from pathlib import Path
from urllib.parse import quote

OUT = Path(sys.argv[1])
DRUGS = json.loads((OUT / "drugs.json").read_text())["drugs"]
CLASSES = json.loads((OUT / "classes.json").read_text())["classes"]
REVIEWED = "2026-11-03"
GRADE_NOTE = "Demo grading, pending pharmacist review"


def dm(generic):
    return f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?labeltype=all&query={quote(generic)}"


def lab(generic, section):
    return {"label": f"FDA label: {generic}, {section}", "url": dm(generic)}


ONC = {"label": "ONC high-priority drug-drug interactions (Phansalkar et al., JAMIA 2012;19:735-743)", "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC3422823/"}
STOPP = {"label": "STOPP/START v3 (O'Mahony et al., Eur Geriatr Med 2023), CC BY 4.0", "url": "https://link.springer.com/article/10.1007/s41999-023-00777-y"}


def sel(drugs=(), classes=()):
    s = {}
    if drugs:
        s["drugs"] = list(drugs)
    if classes:
        s["classes"] = list(classes)
    return s


RULES = []


def rule(rid, selectors, severity, title, effect, management, sources, onc=False, group=None, category="interaction", dose_limit=None):
    RULES.append({
        "id": rid, "all": selectors, "severity": severity, "onc": onc, "title": title,
        "effect": effect, "management": management, "sources": sources, "group": group,
        "category": category, "dose_limit": dose_limit,
    })


# ------------------------------------------------------------------ ONC high-priority list (15)
rule("onc-stimulant-maoi", [sel(classes=["amphetamine_like"]), sel(classes=["maoi"])], "critical",
     "Stimulant with an MAO inhibitor", "Risk of hypertensive crisis.",
     "Do not combine. Stimulant labels contraindicate use during or within 14 days after stopping an MAOI. (The ONC panel notes low-dose selegiline is MAO-B selective.)",
     [ONC, lab("amphetamine-dextroamphetamine", "Contraindications")], onc=True)
rule("onc-atazanavir-ppi", [sel(drugs=["atazanavir"]), sel(classes=["ppi"])], "critical",
     "Atazanavir with a proton pump inhibitor", "PPIs sharply reduce atazanavir absorption, risking loss of virologic response and resistance.",
     "Avoid the combination; follow the atazanavir label for the narrow exceptions.", [ONC, lab("atazanavir", "Drug Interactions")], onc=True)
rule("onc-febuxostat-thiopurine", [sel(drugs=["febuxostat"]), sel(classes=["thiopurine"])], "critical",
     "Febuxostat with azathioprine or mercaptopurine", "Xanthine oxidase inhibition raises thiopurine levels and can cause severe bone-marrow suppression.",
     "Contraindicated per the febuxostat label.", [ONC, lab("febuxostat", "Contraindications")], onc=True)
rule("onc-serotonergic-maoi", [sel(classes=["ssri", "snri", "serotonergic"]), sel(classes=["maoi", "maoi_like"])], "critical",
     "Serotonergic drug with an MAO inhibitor", "Risk of serotonin syndrome (agitation, hyperthermia, clonus, autonomic instability), which can be fatal. Linezolid and methylene blue also inhibit MAO.",
     "Do not combine. Labels require a washout (generally 14 days; 5 weeks after fluoxetine).", [ONC, lab("sertraline", "Contraindications"), lab("linezolid", "Warnings and Precautions")], onc=True, group="serotonin")
rule("onc-irinotecan-cyp3a4", [sel(drugs=["irinotecan"]), sel(classes=["strong_cyp3a4_inhibitor"])], "critical",
     "Irinotecan with a strong CYP3A4 inhibitor", "Raises exposure to irinotecan's active metabolite, with severe diarrhea and neutropenia.",
     "Avoid; the irinotecan label advises stopping strong CYP3A4 inhibitors at least 1 week before therapy.", [ONC, lab("irinotecan", "Drug Interactions")], onc=True)
rule("onc-opioid-maoi", [sel(classes=["opioid"]), sel(classes=["maoi"])], "critical",
     "Opioid with an MAO inhibitor", "Risk of serotonin syndrome or opioid toxicity (respiratory depression, coma).",
     "Avoid; several opioid labels (for example meperidine, tramadol, methadone) contraindicate use with or within 14 days of an MAOI.", [ONC, lab("tramadol", "Contraindications")], onc=True, group="serotonin")
rule("onc-tca-maoi", [sel(classes=["tca"]), sel(classes=["maoi"])], "critical",
     "Tricyclic antidepressant with an MAO inhibitor", "Risk of serotonin syndrome and hypertensive reactions.",
     "Do not combine; observe the label washout periods.", [ONC, lab("amitriptyline", "Contraindications")], onc=True, group="serotonin")
rule("onc-qt-high", [sel(classes=["qt_high"]), sel(classes=["qt_high"])], "critical",
     "Two high-risk QT-prolonging drugs", "Additive QT prolongation with a risk of torsades de pointes and sudden cardiac death.",
     "Avoid the combination where possible. If both are needed, get a baseline ECG, correct potassium and magnesium, and monitor the QTc.", [ONC, lab("amiodarone", "Warnings and Precautions")], onc=True, group="qt", category="qt")
rule("onc-ramelteon-cyp1a2", [sel(drugs=["ramelteon"]), sel(drugs=["fluvoxamine", "amiodarone", "ciprofloxacin"])], "critical",
     "Ramelteon with a strong CYP1A2 inhibitor", "Markedly higher ramelteon levels and excess sedation.",
     "Avoid; the ramelteon label says not to use with fluvoxamine.", [ONC, lab("ramelteon", "Drug Interactions")], onc=True)
rule("onc-inducer-protease", [sel(classes=["strong_cyp3a4_inducer"]), sel(classes=["protease_inhibitor"])], "critical",
     "Strong CYP3A4 inducer with a protease inhibitor", "Loss of antiviral effect and risk of resistance.",
     "Do not combine; check the antiviral's label for contraindicated inducers.", [ONC, lab("ritonavir", "Contraindications")], onc=True)
rule("onc-simva-lova-cyp3a4", [sel(drugs=["simvastatin", "lovastatin"]), sel(classes=["strong_cyp3a4_inhibitor", "protease_inhibitor"], drugs=["erythromycin", "cyclosporine"])], "critical",
     "Simvastatin or lovastatin with a strong CYP3A4 inhibitor", "Large rise in statin levels: myopathy and rhabdomyolysis.",
     "Contraindicated per the simvastatin label (clarithromycin, erythromycin, azole antifungals, HIV protease inhibitors, cyclosporine and others). Hold the statin during a short antibiotic course or choose a non-interacting antibiotic.",
     [ONC, lab("simvastatin", "Contraindications")], onc=True)
rule("onc-ergot-cyp3a4", [sel(classes=["ergot"]), sel(classes=["strong_cyp3a4_inhibitor", "protease_inhibitor"])], "critical",
     "Ergot alkaloid with a strong CYP3A4 inhibitor", "Ergotism: severe vasospasm with limb or cerebral ischemia (boxed warning).",
     "Contraindicated.", [ONC, lab("ergotamine", "Boxed Warning")], onc=True)
rule("onc-tizanidine-cyp1a2", [sel(drugs=["tizanidine"]), sel(classes=["cyp1a2_strong_inhibitor"])], "critical",
     "Tizanidine with a CYP1A2 inhibitor", "Tizanidine levels rise many-fold: severe hypotension, bradycardia and sedation.",
     "Contraindicated with ciprofloxacin and fluvoxamine; avoid other CYP1A2 inhibitors (tizanidine label).", [ONC, lab("tizanidine", "Contraindications")], onc=True)
rule("onc-tranylcypromine-procarbazine", [sel(drugs=["tranylcypromine"]), sel(drugs=["procarbazine"])], "critical",
     "Tranylcypromine with procarbazine", "Additive MAO inhibition: hypertensive crisis.",
     "Do not combine.", [ONC, lab("procarbazine", "Warnings")], onc=True)
rule("onc-triptan-maoi", [sel(drugs=["sumatriptan", "rizatriptan", "zolmitriptan"]), sel(classes=["maoi"])], "critical",
     "Triptan with an MAO inhibitor", "MAO-A inhibitors raise sumatriptan, rizatriptan and zolmitriptan levels; serotonin toxicity.",
     "Contraindicated (triptan labels).", [ONC, lab("sumatriptan", "Contraindications")], onc=True, group="serotonin")

# ------------------------------------------------------------------ bleeding
rule("anticoag-nsaid", [sel(classes=["anticoagulant"]), sel(classes=["nsaid"])], "warning",
     "Anticoagulant with an NSAID", "Higher risk of serious bleeding, especially gastrointestinal.",
     "Avoid if possible; acetaminophen is usually preferred for pain. If an NSAID is needed, use the shortest course, consider GI protection, and monitor for bleeding (and INR with warfarin).",
     [lab("warfarin", "Drug Interactions"), lab("ibuprofen", "Warnings and Precautions: bleeding")], group="bleeding")
rule("anticoag-antiplatelet", [sel(classes=["anticoagulant"]), sel(classes=["antiplatelet"])], "warning",
     "Anticoagulant with an antiplatelet", "Additive bleeding risk.",
     "Confirm there is a current indication for both and how long the combination should last.", [lab("apixaban", "Drug Interactions"), lab("aspirin", "Warnings")], group="bleeding")
rule("anticoag-anticoag-ssri", [sel(classes=["anticoagulant"]), sel(classes=["ssri", "snri"])], "info",
     "Anticoagulant with an SSRI or SNRI", "SSRIs and SNRIs impair platelet function and add to bleeding risk.",
     "Monitor for bleeding.", [lab("sertraline", "Warnings and Precautions: abnormal bleeding")], group="bleeding")
rule("antiplatelet-nsaid", [sel(classes=["antiplatelet"]), sel(classes=["nsaid"])], "warning",
     "Antiplatelet with an NSAID", "Higher risk of GI bleeding; ibuprofen can blunt aspirin's antiplatelet effect.",
     "Avoid regular NSAID use where possible; if needed, consider GI protection. With low-dose aspirin, take ibuprofen at least 30 minutes after immediate-release aspirin or 8 hours before.",
     [lab("ibuprofen", "Drug Interactions"), lab("clopidogrel", "Drug Interactions")], group="bleeding")
rule("nsaid-ssri", [sel(classes=["nsaid"]), sel(classes=["ssri", "snri"])], "info",
     "NSAID with an SSRI or SNRI", "Increased risk of GI bleeding.", "Consider GI protection for longer courses.", [lab("sertraline", "Warnings and Precautions: abnormal bleeding")], group="bleeding")
rule("warfarin-azole", [sel(drugs=["warfarin"]), sel(classes=["azole_antifungal"])], "warning",
     "Warfarin with an azole antifungal", "Azoles (notably fluconazole) inhibit warfarin metabolism: INR rise and bleeding.",
     "Avoid if possible; otherwise check INR within a few days of starting and adjust warfarin.", [lab("warfarin", "Drug Interactions"), lab("fluconazole", "Drug Interactions")])
rule("warfarin-amiodarone", [sel(drugs=["warfarin"]), sel(drugs=["amiodarone"])], "warning",
     "Warfarin with amiodarone", "Amiodarone inhibits warfarin metabolism; the INR rises over weeks.",
     "Monitor INR closely when starting or changing amiodarone; warfarin doses usually need reducing.", [lab("warfarin", "Drug Interactions"), lab("amiodarone", "Drug Interactions")])
rule("warfarin-tmpsmx", [sel(drugs=["warfarin"]), sel(drugs=["sulfamethoxazole-trimethoprim"])], "warning",
     "Warfarin with sulfamethoxazole-trimethoprim", "CYP2C9 inhibition raises the INR; bleeding risk.",
     "Prefer another antibiotic if possible; otherwise check INR within 3–5 days.", [lab("warfarin", "Drug Interactions")])
rule("warfarin-metronidazole", [sel(drugs=["warfarin"]), sel(drugs=["metronidazole"])], "warning",
     "Warfarin with metronidazole", "Metronidazole inhibits warfarin metabolism: INR rise and bleeding.",
     "Avoid if possible; otherwise monitor INR closely.", [lab("warfarin", "Drug Interactions"), lab("metronidazole", "Drug Interactions")])
rule("warfarin-fluoroquinolone", [sel(drugs=["warfarin"]), sel(classes=["fluoroquinolone"])], "info",
     "Warfarin with a fluoroquinolone", "Fluoroquinolones can raise the INR.", "Check INR during the course.", [lab("ciprofloxacin", "Drug Interactions")])
rule("anticoag-inducer", [sel(classes=["vka", "doac"]), sel(classes=["strong_cyp3a4_inducer"])], "warning",
     "Anticoagulant with a strong enzyme inducer", "Lower anticoagulant levels and loss of stroke or clot protection.",
     "DOAC labels advise avoiding combined P-gp and strong CYP3A4 inducers (rifampin, carbamazepine, phenytoin, St John's wort); with warfarin, monitor INR closely.", [lab("apixaban", "Drug Interactions"), lab("rivaroxaban", "Drug Interactions")])
rule("doac-strong-inhibitor", [sel(drugs=["apixaban", "rivaroxaban"]), sel(drugs=["ketoconazole", "itraconazole", "ritonavir", "nirmatrelvir-ritonavir"])], "warning",
     "Apixaban or rivaroxaban with a combined strong CYP3A4 and P-gp inhibitor", "Higher anticoagulant levels and bleeding risk.",
     "Follow the DOAC label: rivaroxaban, avoid; apixaban, the label calls for a dose change or avoidance depending on the current dose.", [lab("apixaban", "Dosage and Administration"), lab("rivaroxaban", "Drug Interactions")], group="bleeding")
rule("ketorolac-nsaid", [sel(drugs=["ketorolac"]), sel(classes=["nsaid"], drugs=["aspirin"])], "critical",
     "Ketorolac with another NSAID or aspirin", "Cumulative risk of serious NSAID adverse effects (bleeding, kidney injury).",
     "Contraindicated per the ketorolac label.", [lab("ketorolac", "Contraindications")], group="bleeding")

# ------------------------------------------------------------------ statins and CYP3A4
rule("simva-gemfibrozil", [sel(drugs=["simvastatin", "lovastatin"]), sel(drugs=["gemfibrozil"])], "critical",
     "Simvastatin or lovastatin with gemfibrozil", "Myopathy and rhabdomyolysis.", "Contraindicated with simvastatin; avoid with lovastatin.", [lab("simvastatin", "Contraindications")])
rule("statin-gemfibrozil", [sel(classes=["statin"]), sel(drugs=["gemfibrozil"])], "warning",
     "Statin with gemfibrozil", "Higher risk of myopathy and rhabdomyolysis.", "Avoid; if a fibrate is needed, fenofibrate interacts less.", [lab("rosuvastatin", "Drug Interactions"), lab("atorvastatin", "Drug Interactions")])
rule("simva-10mg-limit", [sel(drugs=["simvastatin"]), sel(drugs=["verapamil", "diltiazem", "dronedarone"])], "warning",
     "Simvastatin with verapamil, diltiazem or dronedarone", "Higher simvastatin levels; myopathy risk.",
     "The simvastatin label says not to exceed 10 mg daily with these drugs.", [lab("simvastatin", "Dosage and Administration")], dose_limit={"drug": "simvastatin", "max_mg_per_day": 10})
rule("simva-20mg-limit", [sel(drugs=["simvastatin"]), sel(drugs=["amiodarone", "amlodipine", "ranolazine"])], "warning",
     "Simvastatin with amiodarone, amlodipine or ranolazine", "Higher simvastatin levels; myopathy risk.",
     "The simvastatin label says not to exceed 20 mg daily with these drugs.", [lab("simvastatin", "Dosage and Administration")], dose_limit={"drug": "simvastatin", "max_mg_per_day": 20})
rule("atorva-clarithro-itra", [sel(drugs=["atorvastatin"]), sel(drugs=["clarithromycin", "itraconazole"])], "warning",
     "Atorvastatin with clarithromycin or itraconazole", "Higher atorvastatin levels; myopathy risk.",
     "The atorvastatin label says not to exceed 20 mg daily with these drugs.", [lab("atorvastatin", "Dosage and Administration")], dose_limit={"drug": "atorvastatin", "max_mg_per_day": 20})
rule("colchicine-strong", [sel(drugs=["colchicine"]), sel(drugs=["clarithromycin", "ketoconazole", "itraconazole", "ritonavir", "nirmatrelvir-ritonavir", "cyclosporine", "posaconazole", "voriconazole"])], "critical",
     "Colchicine with a strong CYP3A4 or P-gp inhibitor", "Fatal colchicine toxicity has been reported.",
     "Contraindicated in patients with renal or hepatic impairment; otherwise the colchicine label requires a dose reduction. Prefer another antibiotic or hold colchicine.", [lab("colchicine", "Contraindications; Drug Interactions")])
rule("colchicine-moderate", [sel(drugs=["colchicine"]), sel(drugs=["erythromycin", "diltiazem", "verapamil", "fluconazole"])], "warning",
     "Colchicine with a moderate CYP3A4 inhibitor", "Higher colchicine levels and toxicity.", "The colchicine label calls for a dose reduction.", [lab("colchicine", "Drug Interactions")])
rule("ticagrelor-strong", [sel(drugs=["ticagrelor"]), sel(classes=["strong_cyp3a4_inhibitor"])], "critical",
     "Ticagrelor with a strong CYP3A4 inhibitor", "Much higher ticagrelor levels; bleeding.", "Avoid (ticagrelor label).", [lab("ticagrelor", "Drug Interactions")], group="bleeding")
rule("ticagrelor-inducer", [sel(drugs=["ticagrelor"]), sel(classes=["strong_cyp3a4_inducer"])], "warning",
     "Ticagrelor with a strong CYP3A4 inducer", "Lower ticagrelor levels; loss of effect.", "Avoid (ticagrelor label).", [lab("ticagrelor", "Drug Interactions")])
rule("calcineurin-strong", [sel(drugs=["tacrolimus", "cyclosporine"]), sel(classes=["strong_cyp3a4_inhibitor"])], "warning",
     "Tacrolimus or cyclosporine with a strong CYP3A4 inhibitor", "Large rise in immunosuppressant levels: nephrotoxicity and neurotoxicity.", "Avoid or monitor levels closely with the transplant team.", [lab("tacrolimus", "Drug Interactions")])
rule("calcineurin-inducer", [sel(drugs=["tacrolimus", "cyclosporine"]), sel(classes=["strong_cyp3a4_inducer"])], "warning",
     "Tacrolimus or cyclosporine with a strong CYP3A4 inducer", "Lower immunosuppressant levels; rejection risk.", "Avoid or monitor levels closely.", [lab("tacrolimus", "Drug Interactions")])
rule("carbamazepine-macrolide", [sel(drugs=["carbamazepine"]), sel(drugs=["clarithromycin", "erythromycin"])], "warning",
     "Carbamazepine with clarithromycin or erythromycin", "Carbamazepine toxicity (ataxia, dizziness, hyponatremia).", "Prefer azithromycin or another antibiotic; otherwise monitor levels.", [lab("carbamazepine", "Drug Interactions")])
rule("eplerenone-strong", [sel(drugs=["eplerenone"]), sel(classes=["strong_cyp3a4_inhibitor"])], "critical",
     "Eplerenone with a strong CYP3A4 inhibitor", "Much higher eplerenone levels and hyperkalemia.", "Contraindicated (eplerenone label).", [lab("eplerenone", "Contraindications")], group="hyperkalemia")

# ------------------------------------------------------------------ PDE5, nitrates
rule("pde5-nitrate", [sel(classes=["pde5"]), sel(classes=["nitrate"])], "critical",
     "PDE5 inhibitor with a nitrate", "Severe, potentially fatal hypotension.",
     "Contraindicated. Do not give a nitrate within 24 hours of sildenafil or 48 hours of tadalafil.", [lab("sildenafil", "Contraindications"), lab("tadalafil", "Contraindications")])
rule("riociguat-pde5-nitrate", [sel(drugs=["riociguat"]), sel(classes=["pde5", "nitrate"])], "critical",
     "Riociguat with a PDE5 inhibitor or nitrate", "Severe hypotension.", "Contraindicated (riociguat label).", [lab("riociguat", "Contraindications")])
rule("pde5-alpha", [sel(classes=["pde5"]), sel(classes=["alpha_blocker"])], "info",
     "PDE5 inhibitor with an alpha-blocker", "Additive blood-pressure lowering; dizziness or fainting.", "Start the PDE5 inhibitor at the lowest dose once the alpha-blocker is stable.", [lab("tadalafil", "Drug Interactions")])

# ------------------------------------------------------------------ methotrexate, thiopurines
rule("mtx-tmp", [sel(drugs=["methotrexate"]), sel(drugs=["sulfamethoxazole-trimethoprim", "trimethoprim"])], "critical",
     "Methotrexate with trimethoprim", "Additive folate antagonism and reduced methotrexate clearance: pancytopenia, sometimes fatal.",
     "Avoid; choose another antibiotic.", [lab("methotrexate", "Drug Interactions")])
rule("mtx-nsaid", [sel(drugs=["methotrexate"]), sel(classes=["nsaid"])], "info",
     "Methotrexate with an NSAID", "NSAIDs can reduce methotrexate clearance; most serious with high-dose methotrexate.",
     "With low-dose weekly methotrexate, monitor blood counts and kidney function.", [lab("methotrexate", "Drug Interactions")])
rule("allopurinol-thiopurine", [sel(drugs=["allopurinol"]), sel(classes=["thiopurine"])], "critical",
     "Allopurinol with azathioprine or mercaptopurine", "Allopurinol blocks thiopurine breakdown: severe bone-marrow suppression.",
     "The allopurinol label calls for reducing the azathioprine or mercaptopurine dose to one-third to one-quarter of usual, with close blood-count monitoring; consider an alternative.", [lab("allopurinol", "Drug Interactions"), lab("azathioprine", "Drug Interactions")])

# ------------------------------------------------------------------ lithium, digoxin, antiarrhythmics
rule("lithium-nsaid", [sel(classes=["lithium_class"]), sel(classes=["nsaid"])], "warning",
     "Lithium with an NSAID", "NSAIDs raise lithium levels: toxicity (tremor, confusion, arrhythmia).", "Avoid or check lithium levels within about 5 days.", [lab("lithium", "Drug Interactions")])
rule("lithium-raas", [sel(classes=["lithium_class"]), sel(classes=["acei", "arb", "arni"])], "warning",
     "Lithium with an ACE inhibitor or ARB", "Reduced lithium clearance: toxicity.", "Monitor lithium levels closely after starting or changing the dose.", [lab("lithium", "Drug Interactions")])
rule("lithium-thiazide", [sel(classes=["lithium_class"]), sel(classes=["thiazide"])], "warning",
     "Lithium with a thiazide diuretic", "Thiazides reduce lithium clearance: toxicity.", "Avoid or monitor lithium levels closely.", [lab("lithium", "Drug Interactions")])
rule("digoxin-amiodarone", [sel(drugs=["digoxin"]), sel(drugs=["amiodarone", "dronedarone"])], "warning",
     "Digoxin with amiodarone or dronedarone", "Higher digoxin levels: bradycardia, heart block, toxicity.", "The digoxin label advises measuring levels and reducing the digoxin dose when starting these drugs.", [lab("digoxin", "Drug Interactions")])
rule("digoxin-ccb", [sel(drugs=["digoxin"]), sel(drugs=["verapamil", "diltiazem"])], "warning",
     "Digoxin with verapamil or diltiazem", "Higher digoxin levels and additive AV-node slowing.", "Monitor heart rate and digoxin levels.", [lab("digoxin", "Drug Interactions")])
rule("digoxin-clarithromycin", [sel(drugs=["digoxin"]), sel(drugs=["clarithromycin", "itraconazole"])], "warning",
     "Digoxin with clarithromycin or itraconazole", "P-gp inhibition raises digoxin levels.", "Monitor digoxin levels and symptoms.", [lab("digoxin", "Drug Interactions")])
rule("dofetilide-contra", [sel(drugs=["dofetilide"]), sel(drugs=["verapamil", "hydrochlorothiazide", "triamterene-hydrochlorothiazide", "trimethoprim", "sulfamethoxazole-trimethoprim", "cimetidine", "ketoconazole", "prochlorperazine"])], "critical",
     "Dofetilide with a contraindicated drug", "Higher dofetilide levels or hypokalemia: torsades de pointes.", "Contraindicated (dofetilide label).", [lab("dofetilide", "Contraindications")], group="qt", category="qt")

# ------------------------------------------------------------------ RAAS and potassium
rule("arni-acei", [sel(classes=["arni"]), sel(classes=["acei"])], "critical",
     "Sacubitril/valsartan with an ACE inhibitor", "High risk of angioedema.", "Contraindicated. Stop the ACE inhibitor at least 36 hours before starting sacubitril/valsartan.", [lab("sacubitril-valsartan", "Contraindications")])
rule("arni-arb", [sel(classes=["arni"]), sel(classes=["arb"])], "warning",
     "Sacubitril/valsartan with an ARB", "Sacubitril/valsartan already contains valsartan: duplicate angiotensin blockade.", "Do not combine (label).", [lab("sacubitril-valsartan", "Drug Interactions")])
rule("acei-arb", [sel(classes=["acei"]), sel(classes=["arb"])], "warning",
     "ACE inhibitor with an ARB (dual RAAS blockade)", "More hyperkalemia, hypotension and acute kidney injury without outcome benefit in most patients.", "Generally avoid; monitor potassium and creatinine if used.", [lab("lisinopril", "Warnings and Precautions: dual blockade"), lab("losartan", "Drug Interactions")], group="raas")
rule("aliskiren-raas", [sel(classes=["renin_inhibitor"]), sel(classes=["acei", "arb", "arni"])], "critical",
     "Aliskiren with an ACE inhibitor or ARB", "Hyperkalemia, hypotension and kidney injury.", "Contraindicated in patients with diabetes; avoid when eGFR is below 60 (aliskiren label).", [lab("aliskiren", "Contraindications")], group="raas")
rule("ksparing-potassium", [sel(classes=["k_sparing"]), sel(classes=["potassium_supplement"])], "warning",
     "Potassium-sparing diuretic with a potassium supplement", "Risk of severe hyperkalemia.", "Avoid potassium supplements unless hypokalemia is documented; check potassium and creatinine.", [lab("spironolactone", "Drug Interactions")], group="hyperkalemia")
rule("raas-ksparing", [sel(classes=["acei", "arb", "arni", "renin_inhibitor"]), sel(classes=["k_sparing"])], "warning",
     "ACE inhibitor or ARB with a potassium-sparing diuretic", "Risk of hyperkalemia, especially with reduced kidney function.", "Check potassium and creatinine within about a week of starting, then periodically.", [lab("spironolactone", "Warnings and Precautions: hyperkalemia")], group="hyperkalemia")
rule("raas-potassium", [sel(classes=["acei", "arb", "arni", "renin_inhibitor"]), sel(classes=["potassium_supplement"])], "warning",
     "ACE inhibitor or ARB with a potassium supplement", "Risk of hyperkalemia.", "Confirm the supplement is still needed; check potassium.", [lab("lisinopril", "Drug Interactions")], group="hyperkalemia")
rule("raas-ksparing-potassium", [sel(classes=["acei", "arb", "arni"]), sel(classes=["k_sparing"]), sel(classes=["potassium_supplement"])], "critical",
     "ACE inhibitor or ARB, potassium-sparing diuretic and potassium supplement together", "Three potassium-raising drugs: high risk of life-threatening hyperkalemia.", "Avoid the potassium supplement unless hypokalemia is documented; check potassium and creatinine promptly.", [lab("spironolactone", "Drug Interactions"), lab("lisinopril", "Drug Interactions")], group="hyperkalemia")
rule("eplerenone-potassium", [sel(drugs=["eplerenone"]), sel(classes=["potassium_supplement"], drugs=["spironolactone", "amiloride", "triamterene-hydrochlorothiazide"])], "critical",
     "Eplerenone with a potassium supplement or another potassium-sparing diuretic", "Hyperkalemia.", "Contraindicated in hypertension (eplerenone label).", [lab("eplerenone", "Contraindications")], group="hyperkalemia")
rule("trimethoprim-potassium", [sel(drugs=["trimethoprim", "sulfamethoxazole-trimethoprim"]), sel(classes=["acei", "arb", "arni", "k_sparing"])], "info",
     "Trimethoprim with a potassium-raising drug", "Trimethoprim reduces potassium excretion; hyperkalemia in older or kidney-impaired patients.", "Consider checking potassium.", [lab("sulfamethoxazole-trimethoprim", "Warnings and Precautions: hyperkalemia")], group="hyperkalemia")
rule("triple-whammy", [sel(classes=["acei", "arb", "arni"]), sel(classes=["thiazide", "loop", "k_sparing"]), sel(classes=["nsaid"])], "warning",
     "ACE inhibitor or ARB, diuretic and NSAID together", "The 'triple whammy': acute kidney injury.", "Avoid the NSAID if possible; otherwise check creatinine and keep the patient hydrated.", [lab("ibuprofen", "Drug Interactions")])

# ------------------------------------------------------------------ CNS depressants, opioids
rule("opioid-benzo", [sel(classes=["opioid"]), sel(classes=["benzodiazepine"])], "critical",
     "Opioid with a benzodiazepine", "Profound sedation, respiratory depression, coma and death (FDA boxed warning, 2016).",
     "Reserve for patients without alternatives; use the lowest doses and shortest duration, and consider naloxone.", [lab("oxycodone", "Boxed Warning"), lab("alprazolam", "Boxed Warning")], group="cns")
rule("opioid-gabapentinoid", [sel(classes=["opioid"]), sel(classes=["gabapentinoid"])], "warning",
     "Opioid with gabapentin or pregabalin", "Serious breathing problems (FDA safety communication, 2019).", "Start low, monitor for sedation and breathing, consider naloxone.", [lab("gabapentin", "Warnings and Precautions: respiratory depression")], group="cns")
rule("opioid-zdrug", [sel(classes=["opioid"]), sel(classes=["z_drug"])], "warning",
     "Opioid with a sleep medicine", "Additive sedation and respiratory depression.", "Avoid if possible; use the lowest doses.", [lab("zolpidem", "Drug Interactions")], group="cns")
rule("opioid-muscle-relaxant", [sel(classes=["opioid"]), sel(classes=["muscle_relaxant"])], "warning",
     "Opioid with a muscle relaxant", "Additive CNS and respiratory depression.", "Avoid if possible; warn about sedation and driving.", [lab("oxycodone", "Drug Interactions")], group="cns")
rule("benzo-zdrug", [sel(classes=["benzodiazepine"]), sel(classes=["z_drug"])], "warning",
     "Benzodiazepine with a Z-drug", "Duplicate sedative effect: falls, confusion, respiratory depression.", "Avoid combining sedative-hypnotics.", [lab("zolpidem", "Drug Interactions")], group="cns")
rule("naltrexone-opioid", [sel(drugs=["naltrexone"]), sel(classes=["opioid"])], "critical",
     "Naltrexone with an opioid", "Naltrexone blocks opioid analgesia and can precipitate withdrawal.", "Contraindicated in patients receiving opioid analgesics (naltrexone label).", [lab("naltrexone", "Contraindications")])

# ------------------------------------------------------------------ serotonin and CYP2D6
rule("ssri-tramadol", [sel(classes=["ssri", "snri"]), sel(drugs=["tramadol"])], "warning",
     "SSRI or SNRI with tramadol", "Serotonin syndrome and seizure risk. Fluoxetine and paroxetine also block tramadol's activation (CYP2D6), weakening pain relief.",
     "Prefer a non-serotonergic analgesic; if used, start low and teach the warning signs of serotonin syndrome.", [lab("tramadol", "Warnings and Precautions: serotonin syndrome"), lab("sertraline", "Drug Interactions")], group="serotonin")
rule("ssri-triptan", [sel(classes=["ssri", "snri"]), sel(classes=["triptan"])], "info",
     "SSRI or SNRI with a triptan", "Serotonin syndrome has been reported, though rarely.", "Usually acceptable; counsel on warning signs.", [lab("sumatriptan", "Warnings and Precautions")], group="serotonin")
rule("ssri-other-serotonergic", [sel(classes=["ssri", "snri"]), sel(drugs=["trazodone", "mirtazapine", "buspirone", "lithium", "dextromethorphan", "st johns wort", "fentanyl", "methadone", "meperidine", "tapentadol"])], "info",
     "SSRI or SNRI with another serotonergic drug", "Additive serotonergic effects.", "Monitor for serotonin syndrome, especially after dose increases.", [lab("sertraline", "Warnings and Precautions: serotonin syndrome")], group="serotonin")
rule("bupropion-maoi", [sel(drugs=["bupropion"]), sel(classes=["maoi", "maoi_like"])], "critical",
     "Bupropion with an MAO inhibitor", "Hypertensive reactions.", "Contraindicated; allow 14 days after stopping an MAOI (bupropion label).", [lab("bupropion", "Contraindications")])
rule("maoi-sympathomimetic", [sel(classes=["maoi"]), sel(classes=["sympathomimetic"])], "critical",
     "MAO inhibitor with a decongestant", "Hypertensive crisis.", "Avoid pseudoephedrine and phenylephrine with MAOIs.", [lab("phenelzine", "Contraindications")])
rule("tamoxifen-cyp2d6", [sel(drugs=["tamoxifen"]), sel(classes=["strong_cyp2d6_inhibitor"])], "warning",
     "Tamoxifen with a strong CYP2D6 inhibitor", "Less conversion to endoxifen, which may reduce tamoxifen's effect.", "Prefer an antidepressant with little CYP2D6 inhibition.", [lab("tamoxifen", "Drug Interactions")])
rule("ergot-triptan", [sel(classes=["ergot"]), sel(classes=["triptan"])], "critical",
     "Ergot with a triptan", "Additive vasospasm.", "Do not use within 24 hours of each other (triptan labels).", [lab("sumatriptan", "Contraindications")])

# ------------------------------------------------------------------ QT (non-ONC tier)
rule("qt-qt", [sel(classes=["qt_prolonging"]), sel(classes=["qt_prolonging"])], "warning",
     "Two drugs that prolong the QT interval", "Additive QT prolongation; torsades risk rises with low potassium or magnesium, bradycardia, heart disease and older age.",
     "Consider an alternative to one drug; otherwise check an ECG and electrolytes.", [lab("ondansetron", "Warnings and Precautions: QT prolongation"), lab("azithromycin", "Warnings and Precautions: QT prolongation")], group="qt", category="qt")

# ------------------------------------------------------------------ absorption, misc
rule("fq-steroid", [sel(classes=["fluoroquinolone"]), sel(classes=["corticosteroid"])], "warning",
     "Fluoroquinolone with a systemic corticosteroid", "Higher risk of tendinitis and tendon rupture, especially over age 60 (boxed warning).",
     "Prefer another antibiotic when reasonable; counsel to stop at the first tendon pain.", [lab("levofloxacin", "Boxed Warning")])
rule("chelation", [sel(classes=["fluoroquinolone", "tetracycline_class"]), sel(classes=["polyvalent_cation"])], "info",
     "Antibiotic with calcium, iron, magnesium or antacids", "Chelation sharply reduces antibiotic absorption.", "Separate doses (for ciprofloxacin: 2 hours before or 6 hours after).", [lab("ciprofloxacin", "Drug Interactions")])
rule("levothyroxine-cation", [sel(drugs=["levothyroxine"]), sel(classes=["polyvalent_cation"])], "info",
     "Levothyroxine with calcium, iron or antacids", "Reduced levothyroxine absorption.", "Take levothyroxine at least 4 hours apart from these products.", [lab("levothyroxine", "Drug Interactions")])
rule("theophylline-cyp1a2", [sel(drugs=["theophylline"]), sel(classes=["cyp1a2_strong_inhibitor"])], "warning",
     "Theophylline with ciprofloxacin or fluvoxamine", "Theophylline toxicity: seizures, arrhythmias.", "Avoid or monitor theophylline levels.", [lab("theophylline", "Drug Interactions")])
rule("lamotrigine-valproate", [sel(drugs=["lamotrigine"]), sel(drugs=["valproate"])], "warning",
     "Lamotrigine with valproate", "Valproate more than doubles lamotrigine levels; higher risk of serious rash.", "The lamotrigine label has a separate, slower titration for patients on valproate.", [lab("lamotrigine", "Dosage and Administration")])
rule("sulfonylurea-fq", [sel(classes=["sulfonylurea"]), sel(classes=["fluoroquinolone"])], "info",
     "Sulfonylurea with a fluoroquinolone", "Fluoroquinolones can cause serious hypoglycemia (or hyperglycemia).", "Counsel on hypoglycemia; check glucose more often.", [lab("ciprofloxacin", "Warnings and Precautions: blood glucose")])

# ------------------------------------------------------------------ allergy groups
ALLERGY = [
    {"id": "penicillin", "label": "Penicillins", "terms": ["penicillin", "penicillins", "pcn", "pen", "beta-lactam", "amoxicillin", "amoxil", "augmentin", "ampicillin", "dicloxacillin", "nafcillin", "piperacillin"],
     "member_classes": ["penicillin"], "severity": "critical",
     "cross": [{"classes": ["cephalosporin"], "severity": "warning",
                "note": "Cross-sensitivity among beta-lactams is documented (see the cephalosporin label). Risk is highest when side chains are similar, such as amoxicillin or ampicillin with cephalexin or cefadroxil. Review the type of reaction."}],
     "source": lab("cephalexin", "Warnings and Precautions: hypersensitivity")},
    {"id": "cephalosporin", "label": "Cephalosporins", "terms": ["cephalosporin", "cephalosporins", "cephalexin", "keflex", "cefuroxime", "ceftriaxone", "cefdinir", "cefazolin", "cefadroxil", "cefpodoxime"],
     "member_classes": ["cephalosporin"], "severity": "critical",
     "cross": [{"classes": ["penicillin"], "severity": "info", "note": "Some patients allergic to a cephalosporin react to penicillins with a similar side chain."}],
     "source": lab("cephalexin", "Contraindications")},
    {"id": "sulfonamide_antibiotic", "label": "Sulfonamide antibiotics", "terms": ["sulfa", "sulfa drugs", "sulfonamide", "sulfonamides", "bactrim", "septra", "sulfamethoxazole", "tmp-smx", "smx-tmp"],
     "member_classes": ["sulfonamide_antibiotic"], "severity": "critical",
     "cross": [{"drugs": ["celecoxib"], "severity": "critical", "note": "The celecoxib label lists allergic-type reactions to sulfonamides as a contraindication."},
               {"classes": ["nonantibiotic_sulfonamide"], "severity": "info", "note": "Cross-reactivity between sulfonamide antibiotics and non-antibiotic sulfonamides (thiazides, loop diuretics) appears uncommon; labels advise caution."}],
     "source": lab("sulfamethoxazole-trimethoprim", "Contraindications")},
    {"id": "nsaid", "label": "NSAIDs and aspirin", "terms": ["nsaid", "nsaids", "aspirin", "asa", "ibuprofen", "motrin", "advil", "naproxen", "aleve", "naprosyn", "meloxicam", "diclofenac", "ketorolac", "toradol", "indomethacin", "celecoxib", "celebrex"],
     "member_classes": ["nsaid"], "member_drugs": ["aspirin"], "severity": "critical",
     "cross": [],
     "source": lab("ibuprofen", "Contraindications: aspirin or NSAID reactions")},
    {"id": "opioid", "label": "Opioids", "terms": ["opioid", "opioids", "opiate", "opiates", "codeine", "morphine", "oxycodone", "hydrocodone", "tramadol", "hydromorphone", "fentanyl", "percocet", "vicodin", "norco"],
     "member_classes": ["opioid"], "severity": "warning", "same_drug_severity": "critical",
     "cross": [],
     "note": "Many opioid reactions are non-allergic (itching or nausea from histamine release). Document the reaction; cross-sensitivity between opioid chemical classes is lower.",
     "source": lab("morphine", "Contraindications")},
    {"id": "ace_inhibitor", "label": "ACE inhibitors (including angioedema)", "terms": ["ace inhibitor", "ace inhibitors", "acei", "lisinopril", "enalapril", "ramipril", "benazepril", "captopril", "quinapril", "ace inhibitor angioedema"],
     "member_classes": ["acei"], "severity": "critical",
     "cross": [{"classes": ["arni"], "severity": "critical", "note": "Sacubitril/valsartan is contraindicated with a history of angioedema related to ACE inhibitor or ARB therapy."},
               {"classes": ["arb"], "severity": "warning", "note": "ARBs can also cause angioedema; use with caution after ACE inhibitor angioedema."}],
     "source": lab("lisinopril", "Contraindications")},
    {"id": "macrolide", "label": "Macrolides", "terms": ["macrolide", "macrolides", "azithromycin", "zithromax", "z-pak", "z-pack", "clarithromycin", "biaxin", "erythromycin"],
     "member_classes": ["macrolide"], "severity": "critical", "cross": [], "source": lab("azithromycin", "Contraindications")},
    {"id": "fluoroquinolone", "label": "Fluoroquinolones", "terms": ["fluoroquinolone", "fluoroquinolones", "quinolone", "quinolones", "cipro", "ciprofloxacin", "levaquin", "levofloxacin", "moxifloxacin"],
     "member_classes": ["fluoroquinolone"], "severity": "critical", "cross": [], "source": lab("ciprofloxacin", "Contraindications")},
    {"id": "tetracycline", "label": "Tetracyclines", "terms": ["tetracycline", "tetracyclines", "doxycycline", "minocycline"],
     "member_classes": ["tetracycline_class"], "severity": "critical", "cross": [], "source": lab("doxycycline", "Contraindications")},
]

# ------------------------------------------------------------------ drug-condition rules
CONDITIONS = [
    {"id": "nsaid-heart-failure", "drugs": sel(classes=["nsaid"]), "icd10": ["I50"], "terms": ["heart failure", "chf", "hfref", "hfpef", "congestive heart failure"],
     "severity": "warning", "summary": "NSAID in a patient with heart failure", "detail": "NSAIDs cause fluid retention and can worsen heart failure; labels advise avoiding them in severe heart failure unless the benefit outweighs the risk.", "source": lab("ibuprofen", "Warnings and Precautions: heart failure and edema")},
    {"id": "nsaid-ckd", "drugs": sel(classes=["nsaid"]), "icd10": ["N18"], "terms": ["chronic kidney disease", "ckd", "renal insufficiency", "kidney disease"], "egfr_below": 60,
     "severity": "warning", "summary": "NSAID in a patient with chronic kidney disease", "detail": "NSAIDs can reduce kidney function; labels say they are not recommended in advanced renal disease.", "source": lab("ibuprofen", "Warnings and Precautions: renal toxicity")},
    {"id": "nsaid-gi-bleed", "drugs": sel(classes=["nsaid"]), "icd10": ["K25", "K26", "K27", "K28", "K92.0", "K92.1", "K92.2"], "terms": ["peptic ulcer", "gi bleed", "gastrointestinal bleed", "gastrointestinal bleeding", "stomach ulcer", "gastric ulcer", "duodenal ulcer", "upper gi bleed"],
     "severity": "warning", "summary": "NSAID with a history of ulcer or GI bleeding", "detail": "NSAID labels carry a boxed warning for serious GI bleeding; prior ulcer or bleeding greatly raises the risk.", "source": lab("naproxen", "Boxed Warning")},
    {"id": "nsbb-asthma", "drugs": sel(classes=["beta_blocker_nonselective"]), "icd10": ["J45"], "terms": ["asthma"],
     "severity": "critical", "summary": "Non-selective beta-blocker in a patient with asthma", "detail": "Can provoke bronchospasm; labels for propranolol, nadolol, carvedilol, labetalol, sotalol and timolol list bronchial asthma as a contraindication.", "source": lab("propranolol", "Contraindications")},
    {"id": "tzd-heart-failure", "drugs": sel(classes=["tzd"]), "icd10": ["I50"], "terms": ["heart failure", "chf"],
     "severity": "critical", "summary": "Thiazolidinedione in a patient with heart failure", "detail": "Boxed warning: thiazolidinediones cause or worsen heart failure; not recommended in symptomatic heart failure and contraindicated in NYHA class III–IV.", "source": lab("pioglitazone", "Boxed Warning")},
    {"id": "nondhp-hfref", "drugs": sel(classes=["ccb_nondhp"]), "icd10": ["I50.2", "I50.4"], "terms": ["hfref", "reduced ejection fraction", "systolic heart failure"],
     "severity": "warning", "summary": "Verapamil or diltiazem in heart failure with reduced ejection fraction", "detail": "Negative inotropes that can worsen HFrEF; the verapamil label contraindicates use in severe left ventricular dysfunction.", "source": lab("verapamil", "Contraindications")},
    {"id": "fq-myasthenia", "drugs": sel(classes=["fluoroquinolone"]), "icd10": ["G70.0"], "terms": ["myasthenia gravis", "myasthenia"],
     "severity": "critical", "summary": "Fluoroquinolone in a patient with myasthenia gravis", "detail": "Boxed warning: fluoroquinolones may exacerbate muscle weakness in myasthenia gravis; avoid.", "source": lab("ciprofloxacin", "Boxed Warning")},
    {"id": "fq-aneurysm", "drugs": sel(classes=["fluoroquinolone"]), "icd10": ["I71"], "terms": ["aortic aneurysm", "abdominal aortic aneurysm", "thoracic aortic aneurysm", "aaa"],
     "severity": "warning", "summary": "Fluoroquinolone in a patient with an aortic aneurysm", "detail": "Fluoroquinolones increase the risk of aortic aneurysm rupture or dissection; labels advise avoiding them in patients with an aneurysm unless there is no alternative.", "source": lab("levofloxacin", "Warnings and Precautions: aortic aneurysm")},
    {"id": "triptan-cad", "drugs": sel(classes=["triptan"]), "icd10": ["I20", "I21", "I22", "I24", "I25"], "terms": ["coronary artery disease", "cad", "angina", "myocardial infarction", "heart attack", "ischemic heart disease"],
     "severity": "critical", "summary": "Triptan in a patient with coronary artery disease", "detail": "Triptans are contraindicated in ischemic coronary artery disease (labels).", "source": lab("sumatriptan", "Contraindications")},
    {"id": "bupropion-seizure", "drugs": sel(drugs=["bupropion"]), "icd10": ["G40", "F50"], "terms": ["seizure disorder", "epilepsy", "seizures", "bulimia", "anorexia nervosa"],
     "severity": "critical", "summary": "Bupropion with a seizure disorder or eating disorder", "detail": "Contraindicated: bupropion lowers the seizure threshold (bupropion label).", "source": lab("bupropion", "Contraindications")},
    {"id": "tramadol-seizure", "drugs": sel(drugs=["tramadol"]), "icd10": ["G40"], "terms": ["seizure disorder", "epilepsy", "seizures"],
     "severity": "warning", "summary": "Tramadol in a patient with a seizure disorder", "detail": "Tramadol can cause seizures, with higher risk in patients with epilepsy.", "source": lab("tramadol", "Warnings and Precautions: seizure risk")},
    {"id": "antipsychotic-dementia", "drugs": sel(classes=["antipsychotic"]), "icd10": ["F01", "F02", "F03", "G30"], "terms": ["dementia", "alzheimer", "alzheimer's disease"],
     "severity": "warning", "summary": "Antipsychotic in an older patient with dementia", "detail": "Boxed warning: increased mortality in elderly patients with dementia-related psychosis; not approved for that use.", "source": lab("quetiapine", "Boxed Warning")},
    {"id": "anticholinergic-dementia", "drugs": sel(classes=["anticholinergic", "first_gen_antihistamine", "urinary_antimuscarinic"]), "icd10": ["F01", "F02", "F03", "G30"], "terms": ["dementia", "alzheimer", "cognitive impairment"],
     "severity": "warning", "summary": "Strongly anticholinergic drug with dementia or cognitive impairment", "detail": "Anticholinergic drugs worsen cognition and raise delirium risk; STOPP/START v3 advises avoiding them in dementia.", "source": STOPP},
    {"id": "anticholinergic-bph", "drugs": sel(classes=["anticholinergic", "first_gen_antihistamine"]), "icd10": ["N40", "R33"], "terms": ["bph", "benign prostatic hyperplasia", "enlarged prostate", "urinary retention"],
     "severity": "warning", "summary": "Anticholinergic drug with prostate enlargement or urinary retention", "detail": "Can precipitate urinary retention (STOPP/START v3).", "source": STOPP},
    {"id": "acei-angioedema", "drugs": sel(classes=["acei", "arni"]), "icd10": ["T78.3", "D84.1"], "terms": ["angioedema", "hereditary angioedema"],
     "severity": "critical", "summary": "ACE inhibitor or sacubitril/valsartan with a history of angioedema", "detail": "Contraindicated with a history of angioedema (labels).", "source": lab("lisinopril", "Contraindications")},
    {"id": "estrogen-vte", "drugs": sel(classes=["estrogen"]), "icd10": ["I26", "I80", "I81", "I82", "Z86.71"], "terms": ["dvt", "deep vein thrombosis", "pulmonary embolism", "blood clot", "vte"],
     "severity": "critical", "summary": "Estrogen-containing product with a history of venous thromboembolism", "detail": "Contraindicated with active or past VTE (estrogen and combined hormonal contraceptive labels).", "source": lab("estradiol", "Contraindications")},
    {"id": "ksparing-hyperkalemia", "drugs": sel(classes=["k_sparing", "potassium_supplement"]), "icd10": ["E87.5"], "terms": ["hyperkalemia", "high potassium"],
     "severity": "critical", "summary": "Potassium-raising drug in a patient with hyperkalemia", "detail": "Spironolactone and potassium supplements are contraindicated in hyperkalemia (labels).", "source": lab("spironolactone", "Contraindications")},
    {"id": "qt-long-qt", "drugs": sel(classes=["qt_prolonging"]), "icd10": ["I45.81"], "terms": ["long qt", "long qt syndrome", "qt prolongation", "prolonged qt"],
     "severity": "critical", "summary": "QT-prolonging drug in a patient with long QT", "detail": "Avoid drugs that prolong the QT interval in congenital long QT or known QT prolongation.", "source": lab("citalopram", "Warnings and Precautions: QT prolongation")},
    {"id": "pregnancy-fetal", "drugs": sel(classes=["acei", "arb", "arni", "renin_inhibitor"], drugs=["methotrexate", "warfarin", "valproate"]), "icd10": ["Z33.1", "O09"], "terms": ["pregnant", "currently pregnant"], "pregnancy": True,
     "severity": "critical", "summary": "Drug with fetal toxicity in a pregnant patient", "detail": "Boxed or contraindication warnings for use in pregnancy (fetal toxicity). Review alternatives.", "source": lab("lisinopril", "Boxed Warning: fetal toxicity")},
]

# ------------------------------------------------------------------ dose ranges (usual adult, label maximums)
DOSES = {
    # generic: (usual text, max mg/day, note)
    "lisinopril": ("10–40 mg once daily", 80, None),
    "enalapril": ("5–40 mg/day in 1–2 doses", 40, None),
    "losartan": ("25–100 mg/day", 100, None),
    "valsartan": ("80–320 mg/day", 320, None),
    "amlodipine": ("2.5–10 mg once daily", 10, None),
    "metoprolol": ("25–200 mg/day", 450, "Tartrate maximum 450 mg/day; succinate 400 mg/day."),
    "atenolol": ("25–100 mg once daily", 200, "Up to 200 mg/day for angina."),
    "carvedilol": ("3.125–25 mg twice daily", 100, "50 mg twice daily only for heart failure over 85 kg."),
    "propranolol": ("40–320 mg/day", 640, None),
    "hydrochlorothiazide": ("12.5–50 mg once daily", 50, None),
    "chlorthalidone": ("12.5–25 mg once daily", 100, None),
    "furosemide": ("20–80 mg/day", 600, "Up to 600 mg/day in severe edema."),
    "spironolactone": ("25–100 mg/day", 400, "Higher doses only for primary hyperaldosteronism or edema."),
    "hydralazine": ("10–50 mg four times daily", 300, None),
    "clonidine": ("0.1–0.3 mg twice daily", 2.4, None),
    "atorvastatin": ("10–80 mg once daily", 80, None),
    "simvastatin": ("5–40 mg once daily", 40, "80 mg is restricted to patients already tolerating it for 12 months."),
    "rosuvastatin": ("5–40 mg once daily", 40, None),
    "pravastatin": ("10–80 mg once daily", 80, None),
    "lovastatin": ("10–80 mg/day", 80, None),
    "metformin": ("500–2,000 mg/day in divided doses", 2550, "Extended-release maximum 2,000 mg/day."),
    "glipizide": ("2.5–20 mg/day", 40, "Extended-release maximum 20 mg/day."),
    "glyburide": ("1.25–20 mg/day", 20, "Micronized maximum 12 mg/day."),
    "glimepiride": ("1–8 mg once daily", 8, None),
    "pioglitazone": ("15–45 mg once daily", 45, None),
    "sitagliptin": ("100 mg once daily", 100, "Reduced in kidney impairment."),
    "empagliflozin": ("10–25 mg once daily", 25, None),
    "dapagliflozin": ("5–10 mg once daily", 10, None),
    "aspirin": ("81 mg/day (antiplatelet); 325–650 mg every 4–6 h for pain", 4000, None),
    "acetaminophen": ("325–1,000 mg every 4–6 hours", 4000, "Many OTC labels advise no more than 3,000 mg/day."),
    "ibuprofen": ("200–800 mg three to four times daily", 3200, "OTC maximum 1,200 mg/day."),
    "naproxen": ("250–500 mg twice daily", 1500, "1,500 mg/day only for limited periods."),
    "meloxicam": ("7.5–15 mg once daily", 15, None),
    "celecoxib": ("100–200 mg once or twice daily", 400, None),
    "diclofenac": ("50 mg two to three times daily", 200, None),
    "ketorolac": ("10 mg every 4–6 hours (oral)", 40, "Oral maximum 40 mg/day; total course no more than 5 days."),
    "gabapentin": ("300–1,200 mg three times daily", 3600, None),
    "pregabalin": ("75–300 mg twice daily", 600, None),
    "tramadol": ("50–100 mg every 4–6 hours", 400, "Over 75 years: maximum 300 mg/day; extended-release maximum 300 mg/day."),
    "sertraline": ("50–200 mg once daily", 200, None),
    "fluoxetine": ("20–80 mg/day", 80, None),
    "paroxetine": ("20–50 mg/day", 60, "Up to 60 mg/day for panic disorder."),
    "citalopram": ("20–40 mg once daily", 40, "Maximum 20 mg/day over age 60 (QT prolongation)."),
    "escitalopram": ("10–20 mg once daily", 20, None),
    "duloxetine": ("40–60 mg/day", 120, None),
    "venlafaxine": ("75–225 mg/day", 375, "Extended-release maximum 225 mg/day."),
    "bupropion": ("150–300 mg/day", 450, "SR maximum 400 mg/day."),
    "trazodone": ("50–400 mg/day", 400, "Up to 600 mg/day in hospitalized patients."),
    "mirtazapine": ("15–45 mg at bedtime", 45, None),
    "quetiapine": ("50–800 mg/day", 800, None),
    "alprazolam": ("0.25–0.5 mg three times daily", 10, "Panic disorder up to 10 mg/day; anxiety usually no more than 4 mg/day."),
    "lorazepam": ("2–6 mg/day in divided doses", 10, None),
    "zolpidem": ("5–10 mg at bedtime", 10, "Women and older adults: 5 mg."),
    "cyclobenzaprine": ("5–10 mg three times daily", 30, None),
    "tizanidine": ("2–8 mg every 6–8 hours", 36, None),
    "baclofen": ("5–20 mg three times daily", 80, None),
    "montelukast": ("10 mg once daily", 10, None),
    "allopurinol": ("100–300 mg/day", 800, None),
    "colchicine": ("0.6 mg once or twice daily (prophylaxis)", 1.8, "Flare: 1.2 mg then 0.6 mg an hour later (1.8 mg total)."),
    "sildenafil": ("25–100 mg once daily as needed (ED)", 100, "Pulmonary hypertension dosing differs (20 mg three times daily)."),
    "tadalafil": ("5–20 mg as needed (ED)", 20, None),
    "cephalexin": ("250–500 mg every 6 hours", 4000, None),
    "ciprofloxacin": ("250–750 mg twice daily", 1500, None),
    "levofloxacin": ("250–750 mg once daily", 750, None),
    "doxycycline": ("100 mg twice daily", 200, None),
    "nitrofurantoin": ("100 mg twice daily (Macrobid)", 400, None),
    "clarithromycin": ("250–500 mg twice daily", 1000, None),
    "metronidazole": ("500 mg two to three times daily", 4000, None),
    "valacyclovir": ("500 mg–1 g two to three times daily", 4000, None),
    "hydroxyzine": ("25 mg three to four times daily", 400, None),
    "diphenhydramine": ("25–50 mg every 4–6 hours", 300, None),
    "meclizine": ("25–100 mg/day", 100, None),
    "ondansetron": ("4–8 mg two to three times daily", 24, None),
    "sumatriptan": ("25–100 mg per dose (oral)", 200, None),
    "donepezil": ("5–10 mg at bedtime", 23, None),
    "memantine": ("5–10 mg twice daily", 28, "Extended-release maximum 28 mg/day."),
    "tamsulosin": ("0.4–0.8 mg once daily", 0.8, None),
    "finasteride": ("5 mg once daily (BPH)", 5, None),
    "oxybutynin": ("5 mg two to three times daily", 30, "Immediate-release maximum 20 mg/day."),
}
DOSE_SPECIAL = {
    "methotrexate": {"weekly_only": True, "usual": "7.5–25 mg once WEEKLY (rheumatoid arthritis, psoriasis)",
                      "detail": "Methotrexate for non-cancer use is taken once a week. Daily dosing by mistake has caused fatal toxicity (label boxed warning)."},
}

# ------------------------------------------------------------------ renal label thresholds (show label text; never compute a dose)
RENAL = {
    "metformin": [
        {"below": 30, "severity": "critical", "summary": "Metformin is contraindicated at eGFR below 30", "text": "Metformin label: contraindicated in patients with an eGFR below 30 mL/min/1.73 m²; discontinue if eGFR falls below 30."},
        {"below": 45, "severity": "warning", "summary": "Metformin at eGFR 30–44", "text": "Metformin label: starting metformin is not recommended at eGFR 30–45; for patients already taking it whose eGFR falls below 45, assess the benefit and risk of continuing."},
    ],
    "empagliflozin": [{"below": 30, "severity": "info", "summary": "Empagliflozin at low eGFR", "text": "Empagliflozin label: likely ineffective for glucose lowering at eGFR below 30; review the label's renal dosing section for other indications."}],
    "dapagliflozin": [{"below": 25, "severity": "info", "summary": "Dapagliflozin at eGFR below 25", "text": "Dapagliflozin label: starting is not recommended at eGFR below 25; review the label for patients already taking it."}],
    "canagliflozin": [{"below": 30, "severity": "warning", "summary": "Canagliflozin at eGFR below 30", "text": "Canagliflozin label: starting is not recommended at eGFR below 30."}],
    "sitagliptin": [{"below": 45, "severity": "warning", "summary": "Sitagliptin needs a renal dose adjustment", "text": "Sitagliptin label: the dose is reduced at eGFR 30–45 and further below 30. Review the label dosing table."}],
    "apixaban": [{"below": 60, "severity": "info", "summary": "Check apixaban dose criteria", "text": "Apixaban label (atrial fibrillation): the reduced dose applies to patients with any 2 of age 80 or over, weight 60 kg or less, or serum creatinine 1.5 mg/dL or more. Review the label."}],
    "rivaroxaban": [{"below": 50, "severity": "warning", "summary": "Rivaroxaban dose depends on creatinine clearance", "text": "Rivaroxaban label uses creatinine clearance (Cockcroft-Gault), not eGFR, to set the dose in atrial fibrillation. Review the label."}],
    "dabigatran": [{"below": 50, "severity": "warning", "summary": "Dabigatran dose depends on creatinine clearance", "text": "Dabigatran label: dosing depends on creatinine clearance, with reduced dosing at CrCl 15–30 mL/min and use not recommended below 15. Review the label."}],
    "edoxaban": [{"below": 50, "severity": "warning", "summary": "Edoxaban dose depends on creatinine clearance", "text": "Edoxaban label: dose is reduced at CrCl 15–50 mL/min; review the label."}],
    "gabapentin": [{"below": 60, "severity": "warning", "summary": "Gabapentin needs a renal dose adjustment", "text": "Gabapentin label: the daily dose range is reduced when creatinine clearance is below 60 mL/min. Review the label dosing table."}],
    "pregabalin": [{"below": 60, "severity": "warning", "summary": "Pregabalin needs a renal dose adjustment", "text": "Pregabalin label: reduce the dose when creatinine clearance is below 60 mL/min. Review the label dosing table."}],
    "nitrofurantoin": [{"below": 60, "severity": "warning", "summary": "Nitrofurantoin with reduced kidney function", "text": "Nitrofurantoin label: contraindicated with creatinine clearance under 60 mL/min or clinically significant creatinine elevation."}],
    "spironolactone": [{"below": 45, "severity": "warning", "summary": "Spironolactone with reduced kidney function", "text": "Spironolactone label: hyperkalemia risk rises with kidney impairment; in heart failure with eGFR 30–50, the label suggests a lower starting dose. Monitor potassium closely."}],
    "tramadol": [{"below": 30, "severity": "warning", "summary": "Tramadol with creatinine clearance below 30", "text": "Tramadol label: with creatinine clearance below 30 mL/min, lengthen the dosing interval to every 12 hours with a maximum of 200 mg/day."}],
    "colchicine": [{"below": 30, "severity": "warning", "summary": "Colchicine in severe kidney impairment", "text": "Colchicine label: dose limits apply in severe renal impairment; review the label."}],
    "ibuprofen": [{"below": 30, "severity": "warning", "summary": "NSAID in advanced kidney disease", "text": "NSAID labels: not recommended in advanced renal disease unless the benefits outweigh the risks."}],
    "naproxen": [{"below": 30, "severity": "warning", "summary": "NSAID in advanced kidney disease", "text": "NSAID labels: not recommended in advanced renal disease unless the benefits outweigh the risks."}],
    "meloxicam": [{"below": 30, "severity": "warning", "summary": "NSAID in advanced kidney disease", "text": "NSAID labels: not recommended in advanced renal disease unless the benefits outweigh the risks."}],
    "celecoxib": [{"below": 30, "severity": "warning", "summary": "NSAID in advanced kidney disease", "text": "NSAID labels: not recommended in advanced renal disease unless the benefits outweigh the risks."}],
    "diclofenac": [{"below": 30, "severity": "warning", "summary": "NSAID in advanced kidney disease", "text": "NSAID labels: not recommended in advanced renal disease unless the benefits outweigh the risks."}],
    "valacyclovir": [{"below": 50, "severity": "info", "summary": "Valacyclovir needs a renal dose adjustment", "text": "Valacyclovir label: reduce the dose when creatinine clearance is below 50 mL/min. Review the label dosing table."}],
    "levofloxacin": [{"below": 50, "severity": "info", "summary": "Levofloxacin needs a renal dose adjustment", "text": "Levofloxacin label: adjust the dose when creatinine clearance is below 50 mL/min."}],
    "ciprofloxacin": [{"below": 50, "severity": "info", "summary": "Ciprofloxacin needs a renal dose adjustment", "text": "Ciprofloxacin label: adjust the dose when creatinine clearance is 50 mL/min or below."}],
    "lithium": [{"below": 60, "severity": "warning", "summary": "Lithium with reduced kidney function", "text": "Lithium label: renal impairment raises the risk of lithium toxicity; monitor levels and kidney function closely."}],
    "digoxin": [{"below": 50, "severity": "warning", "summary": "Digoxin with reduced kidney function", "text": "Digoxin label: digoxin is cleared by the kidneys; impairment raises levels. Review the label and monitor levels."}],
}

# ------------------------------------------------------------------ older adults (STOPP/START v3); age >= 65
OLDER = [
    {"id": "older-first-gen-antihistamine", "drugs": sel(classes=["first_gen_antihistamine"]), "severity": "warning",
     "summary": "First-generation antihistamine in an older adult", "detail": "Strongly anticholinergic and sedating: confusion, falls, constipation and urinary retention. STOPP/START v3 advises avoiding it; a non-sedating antihistamine, or sleep measures for insomnia, are alternatives."},
    {"id": "older-benzodiazepine", "drugs": sel(classes=["benzodiazepine"]), "severity": "warning",
     "summary": "Benzodiazepine in an older adult", "detail": "Raises the risk of sedation, confusion, falls and fractures; STOPP/START v3 flags use beyond 4 weeks and in patients who fall."},
    {"id": "older-z-drug", "drugs": sel(classes=["z_drug"]), "severity": "warning",
     "summary": "Z-drug hypnotic in an older adult", "detail": "Z-drugs raise the risk of falls and fractures (STOPP/START v3)."},
    {"id": "older-long-sulfonylurea", "drugs": sel(classes=["long_acting_sulfonylurea"]), "severity": "warning",
     "summary": "Long-acting sulfonylurea in an older adult", "detail": "Risk of prolonged hypoglycemia; STOPP/START v3 names glibenclamide (glyburide) and glimepiride."},
    {"id": "older-tca", "drugs": sel(classes=["tca"]), "severity": "info",
     "summary": "Tricyclic antidepressant in an older adult", "detail": "Anticholinergic, sedating and can cause orthostatic hypotension and falls (STOPP/START v3)."},
    {"id": "older-nsaid", "drugs": sel(classes=["nsaid"]), "severity": "info",
     "summary": "NSAID in an older adult", "detail": "Higher risk of GI bleeding, kidney injury and heart failure; STOPP/START v3 flags NSAIDs with reduced kidney function, heart failure or an anticoagulant."},
]

PREVENTION = {
    "note": "Reminders computed from age and sex only; confirm what has already been done in the record.",
    "sources": {
        "uspstf": {"label": "USPSTF A and B recommendations", "url": "https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics/uspstf-a-and-b-recommendations"},
        "cdc": {"label": "CDC adult immunization schedule", "url": "https://www.cdc.gov/vaccines/hcp/imz-schedules/adult-age.html"},
    },
    "items": [
        {"id": "crc", "name": "Colorectal cancer screening", "min_age": 45, "max_age": 75, "sex": None, "source": "uspstf", "detail": "Adults 45–75 (USPSTF A for 50–75, B for 45–49, May 2021)."},
        {"id": "breast", "name": "Breast cancer screening", "min_age": 40, "max_age": 74, "sex": "F", "source": "uspstf", "detail": "Biennial mammography for women 40–74 (USPSTF B, April 2024)."},
        {"id": "cervical", "name": "Cervical cancer screening", "min_age": 21, "max_age": 65, "sex": "F", "source": "uspstf", "detail": "Cytology every 3 years from 21; from 30, hrHPV every 5 years or cotesting (USPSTF A, August 2018)."},
        {"id": "lung", "name": "Lung cancer screening", "min_age": 50, "max_age": 80, "sex": None, "source": "uspstf", "requires": "smoking", "detail": "Annual low-dose CT for adults 50–80 with a 20 pack-year history who smoke or quit within 15 years (USPSTF B, March 2021)."},
        {"id": "aaa", "name": "Abdominal aortic aneurysm screening", "min_age": 65, "max_age": 75, "sex": "M", "source": "uspstf", "requires": "ever_smoked", "detail": "One-time ultrasound for men 65–75 who have ever smoked (USPSTF B, December 2019)."},
        {"id": "diabetes", "name": "Prediabetes and type 2 diabetes screening", "min_age": 35, "max_age": 70, "sex": None, "source": "uspstf", "requires": "overweight", "detail": "Adults 35–70 with overweight or obesity (USPSTF B, August 2021)."},
        {"id": "osteoporosis", "name": "Osteoporosis screening", "min_age": 65, "max_age": None, "sex": "F", "source": "uspstf", "detail": "Women 65 and older (USPSTF B, January 2025)."},
        {"id": "hcv", "name": "Hepatitis C screening (once)", "min_age": 18, "max_age": 79, "sex": None, "source": "uspstf", "detail": "Adults 18–79 (USPSTF B, March 2020)."},
        {"id": "hiv", "name": "HIV screening", "min_age": 15, "max_age": 65, "sex": None, "source": "uspstf", "detail": "Adolescents and adults 15–65 (USPSTF A, June 2019)."},
        {"id": "influenza", "name": "Influenza vaccine", "min_age": 19, "max_age": None, "sex": None, "source": "cdc", "detail": "One dose every season for all adults."},
        {"id": "tdap", "name": "Tdap/Td booster", "min_age": 19, "max_age": None, "sex": None, "source": "cdc", "detail": "Tdap once, then Td or Tdap every 10 years."},
        {"id": "zoster", "name": "Recombinant zoster vaccine", "min_age": 50, "max_age": None, "sex": None, "source": "cdc", "detail": "Two doses for adults 50 and older."},
        {"id": "pneumococcal", "name": "Pneumococcal vaccine", "min_age": 50, "max_age": None, "sex": None, "source": "cdc", "detail": "Adults 50 and older (PCV20 or PCV21 alone, or PCV15 followed by PPSV23)."},
        {"id": "rsv", "name": "RSV vaccine", "min_age": 75, "max_age": None, "sex": None, "source": "cdc", "detail": "Adults 75 and older; also 50–74 at increased risk."},
    ],
}

# ------------------------------------------------------------------ validation
def check_sel(s, where):
    for d in s.get("drugs", []):
        assert d in DRUGS, f"{where}: unknown drug {d}"
    for c in s.get("classes", []):
        assert c in CLASSES, f"{where}: unknown class {c}"
    assert s, f"{where}: empty selector"


ids = set()
for r in RULES:
    assert r["id"] not in ids, r["id"]
    ids.add(r["id"])
    assert r["severity"] in ("critical", "warning", "info")
    assert 2 <= len(r["all"]) <= 3
    for s in r["all"]:
        check_sel(s, r["id"])
    for src in r["sources"]:
        assert src["url"].startswith("https://")
for a in ALLERGY:
    for c in a.get("member_classes", []):
        assert c in CLASSES, a["id"]
    for d in a.get("member_drugs", []):
        assert d in DRUGS, a["id"]
    for x in a.get("cross", []):
        check_sel({k: v for k, v in x.items() if k in ("drugs", "classes")}, a["id"] + " cross")
for c in CONDITIONS:
    check_sel(c["drugs"], c["id"])
for g in list(DOSES) + list(DOSE_SPECIAL) + list(RENAL):
    assert g in DRUGS, f"dose/renal: unknown drug {g}"
for o in OLDER:
    check_sel(o["drugs"], o["id"])

meta = {"version": REVIEWED, "grading": GRADE_NOTE}
(OUT / "interactions.json").write_text(json.dumps({**meta, "rules": RULES}, indent=1, ensure_ascii=False) + "\n")
(OUT / "allergies.json").write_text(json.dumps({**meta, "groups": ALLERGY}, indent=1, ensure_ascii=False) + "\n")
(OUT / "conditions.json").write_text(json.dumps({**meta, "rules": CONDITIONS}, indent=1, ensure_ascii=False) + "\n")
(OUT / "doses.json").write_text(json.dumps({**meta, "note": "Usual adult ranges and label maximum daily doses (mg/day). Checks flag doses above the label maximum; they never compute or recommend a dose.",
                                            "doses": {g: {"usual": u, "max_mg_per_day": m, "note": n, "source": lab(g, "Dosage and Administration")} for g, (u, m, n) in DOSES.items()},
                                            "special": {g: {**v, "source": lab(g, "Dosage and Administration; Boxed Warning")} for g, v in DOSE_SPECIAL.items()}}, indent=1, ensure_ascii=False) + "\n")
(OUT / "renal.json").write_text(json.dumps({**meta, "note": "eGFR thresholds at which the label's renal text is shown. The app never computes a renal dose.",
                                            "drugs": {g: [{**t, "source": lab(g, "Dosage and Administration: renal impairment")} for t in v] for g, v in RENAL.items()}}, indent=1, ensure_ascii=False) + "\n")
(OUT / "older_adult.json").write_text(json.dumps({**meta, "min_age": 65, "source": STOPP, "rules": OLDER}, indent=1, ensure_ascii=False) + "\n")
(OUT / "prevention.json").write_text(json.dumps({"version": REVIEWED, **PREVENTION}, indent=1, ensure_ascii=False) + "\n")
print(len(RULES), "interaction rules;", len(ALLERGY), "allergy groups;", len(CONDITIONS), "condition rules;", len(DOSES), "dose ranges;", len(RENAL), "renal drugs;", len(OLDER), "older-adult rules")
