"""Generates app/drugs/kb/drugs.json and classes.json (one-off authoring helper).

Facts encoded here are limited to well-established US labelling: generic names,
US brand names, pharmacologic classes, federal DEA schedules, boxed-warning
presence and ISMP-style confused-name pairs. RxCUIs are only given where the
ingredient RxCUI is well known; everything else resolves live via RxNav.
"""
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])

CLASSES = {
    # id: (label, duplicate-therapy severity or None)
    "anticoagulant": ("Anticoagulant", "critical"),
    "vka": ("Vitamin K antagonist", None),
    "doac": ("Direct oral anticoagulant", None),
    "heparin_class": ("Heparin / low-molecular-weight heparin", None),
    "antiplatelet": ("Antiplatelet", None),
    "p2y12": ("P2Y12 inhibitor", "warning"),
    "nsaid": ("NSAID", "warning"),
    "cox2": ("COX-2 selective NSAID", None),
    "ssri": ("SSRI", "warning"),
    "snri": ("SNRI", "warning"),
    "tca": ("Tricyclic antidepressant", "warning"),
    "maoi": ("MAO inhibitor", "warning"),
    "maoi_like": ("Drug with MAO-inhibiting activity", None),
    "serotonergic": ("Serotonergic drug", None),
    "triptan": ("Triptan", "warning"),
    "ergot": ("Ergot alkaloid", None),
    "opioid": ("Opioid", "warning"),
    "benzodiazepine": ("Benzodiazepine", "warning"),
    "z_drug": ("Z-drug hypnotic", "warning"),
    "gabapentinoid": ("Gabapentinoid", "warning"),
    "muscle_relaxant": ("Skeletal muscle relaxant", "warning"),
    "acei": ("ACE inhibitor", "warning"),
    "arb": ("Angiotensin receptor blocker", "warning"),
    "arni": ("ARNI (sacubitril/valsartan)", None),
    "renin_inhibitor": ("Direct renin inhibitor", None),
    "k_sparing": ("Potassium-sparing diuretic / MRA", "warning"),
    "potassium_supplement": ("Potassium supplement", "warning"),
    "thiazide": ("Thiazide or thiazide-like diuretic", "warning"),
    "loop": ("Loop diuretic", "warning"),
    "beta_blocker": ("Beta-blocker", "warning"),
    "beta_blocker_nonselective": ("Non-selective beta-blocker", None),
    "ccb_dhp": ("Dihydropyridine calcium channel blocker", "warning"),
    "ccb_nondhp": ("Non-dihydropyridine calcium channel blocker", "warning"),
    "alpha_blocker": ("Alpha-1 blocker", "warning"),
    "statin": ("Statin", "warning"),
    "fibrate": ("Fibrate", None),
    "nitrate": ("Nitrate", None),
    "pde5": ("PDE5 inhibitor", "warning"),
    "antiarrhythmic": ("Antiarrhythmic", None),
    "cardiac_glycoside": ("Cardiac glycoside", None),
    "biguanide": ("Biguanide", None),
    "sulfonylurea": ("Sulfonylurea", "warning"),
    "long_acting_sulfonylurea": ("Long-acting sulfonylurea", None),
    "sglt2": ("SGLT2 inhibitor", "warning"),
    "dpp4": ("DPP-4 inhibitor", "warning"),
    "glp1": ("GLP-1 (or GIP/GLP-1) receptor agonist", "warning"),
    "tzd": ("Thiazolidinedione", "warning"),
    "insulin": ("Insulin", None),
    "thyroid": ("Thyroid hormone", None),
    "ppi": ("Proton pump inhibitor", "warning"),
    "h2ra": ("H2 blocker", None),
    "penicillin": ("Penicillin", None),
    "cephalosporin": ("Cephalosporin", None),
    "macrolide": ("Macrolide", None),
    "fluoroquinolone": ("Fluoroquinolone", "warning"),
    "tetracycline_class": ("Tetracycline", None),
    "sulfonamide_antibiotic": ("Sulfonamide antibiotic", None),
    "nonantibiotic_sulfonamide": ("Non-antibiotic sulfonamide", None),
    "azole_antifungal": ("Azole antifungal", None),
    "strong_cyp3a4_inhibitor": ("Strong CYP3A4 inhibitor", None),
    "moderate_cyp3a4_inhibitor": ("Moderate CYP3A4 inhibitor", None),
    "strong_cyp3a4_inducer": ("Strong CYP3A4 inducer", None),
    "protease_inhibitor": ("HIV/COVID protease inhibitor or booster", None),
    "cyp1a2_strong_inhibitor": ("Strong CYP1A2 inhibitor", None),
    "strong_cyp2d6_inhibitor": ("Strong CYP2D6 inhibitor", None),
    "pgp_inhibitor": ("P-glycoprotein inhibitor", None),
    "qt_prolonging": ("Drug whose label warns of QT prolongation", None),
    "qt_high": ("High-risk QT-prolonging drug", None),
    "first_gen_antihistamine": ("First-generation antihistamine", "warning"),
    "second_gen_antihistamine": ("Second-generation antihistamine", None),
    "anticholinergic": ("Strongly anticholinergic drug", None),
    "antipsychotic": ("Antipsychotic", "warning"),
    "amphetamine_like": ("Amphetamine or derivative / stimulant", "warning"),
    "sympathomimetic": ("Sympathomimetic decongestant", None),
    "thiopurine": ("Thiopurine", None),
    "xanthine_oxidase_inhibitor": ("Xanthine oxidase inhibitor", "warning"),
    "corticosteroid": ("Systemic corticosteroid", "warning"),
    "inhaled_corticosteroid": ("Inhaled corticosteroid", None),
    "saba": ("Short-acting beta agonist", None),
    "laba": ("Long-acting beta agonist", None),
    "lama": ("Long-acting muscarinic antagonist", None),
    "leukotriene": ("Leukotriene receptor antagonist", None),
    "lithium_class": ("Lithium", None),
    "antiepileptic": ("Antiepileptic", None),
    "immunosuppressant": ("Immunosuppressant", None),
    "polyvalent_cation": ("Calcium/iron/magnesium/aluminum product", None),
    "estrogen": ("Estrogen-containing product", None),
    "antiviral": ("Antiviral", None),
    "antiemetic": ("Antiemetic", None),
    "bisphosphonate": ("Bisphosphonate", None),
    "dementia_drug": ("Cholinesterase inhibitor / memantine", None),
    "urinary_antimuscarinic": ("Urinary antimuscarinic", "warning"),
    "five_ari": ("5-alpha-reductase inhibitor", None),
    "smoking_cessation": ("Smoking cessation", None),
    "opioid_antagonist": ("Opioid antagonist", None),
    "analgesic_other": ("Analgesic (non-opioid, non-NSAID)", None),
    "antiplatelet_other": ("Antiplatelet (other)", None),
    "lipid_other": ("Lipid-lowering (non-statin)", None),
    "antianginal": ("Antianginal", None),
    "vasodilator": ("Direct vasodilator", None),
    "central_alpha_agonist": ("Central alpha-2 agonist", None),
    "gout_other": ("Gout therapy", None),
    "chemotherapy": ("Antineoplastic", None),
    "antihistamine_any": ("Antihistamine", None),
    "anticonvulsant_sodium": ("Sodium-channel antiepileptic", None),
    "laxative": ("Laxative", None),
    "antidiarrheal": ("Antidiarrheal", None),
    "nitrofuran": ("Nitrofuran antibiotic", None),
    "lincosamide": ("Lincosamide", None),
    "glycopeptide": ("Glycopeptide", None),
    "oxazolidinone": ("Oxazolidinone", None),
    "antitussive": ("Antitussive", None),
    "expectorant": ("Expectorant", None),
    "vitamin": ("Vitamin / mineral", None),
    "hormone_other": ("Hormone", None),
    "androgen": ("Androgen", None),
    "serm": ("Selective estrogen receptor modulator", None),
    "antimalarial": ("Antimalarial / DMARD", None),
    "dmard": ("DMARD", None),
    "pde4": ("PDE4 inhibitor", None),
}

D = []


def drug(generic, brands=(), classes=(), **kw):
    entry = {"generic": generic, "brands": list(brands), "classes": list(classes)}
    entry.update(kw)
    D.append(entry)


FM = ["family_medicine", "internal_medicine"]
CARD = ["cardiology"]
ENDO = ["endocrinology"]
PULM = ["pulmonology"]
ALL = FM + CARD + ENDO + PULM

# ---------------------------------------------------------------- anticoagulants / antiplatelets
drug("warfarin", ["Coumadin", "Jantoven"], ["anticoagulant", "vka"], hr="anticoagulant", boxed=True, rxcui="11289", spec=FM + CARD, uses=["atrial fibrillation", "afib", "clot", "dvt", "pulmonary embolism", "valve"])
drug("apixaban", ["Eliquis"], ["anticoagulant", "doac"], hr="anticoagulant", boxed=True, rxcui="1364430", spec=FM + CARD, uses=["atrial fibrillation", "afib", "clot", "dvt", "pulmonary embolism"])
drug("rivaroxaban", ["Xarelto"], ["anticoagulant", "doac"], hr="anticoagulant", boxed=True, rxcui="1114195", spec=FM + CARD, uses=["atrial fibrillation", "afib", "clot", "dvt"])
drug("dabigatran", ["Pradaxa"], ["anticoagulant", "doac"], hr="anticoagulant", boxed=True, rxcui="1037042", spec=CARD, uses=["atrial fibrillation", "clot"])
drug("edoxaban", ["Savaysa"], ["anticoagulant", "doac"], hr="anticoagulant", boxed=True, spec=CARD)
drug("enoxaparin", ["Lovenox"], ["anticoagulant", "heparin_class"], hr="anticoagulant", boxed=True, rxcui="67108", spec=CARD)
drug("heparin", [], ["anticoagulant", "heparin_class"], hr="anticoagulant", rxcui="5224", spec=CARD)
drug("aspirin", ["Bayer", "Ecotrin"], ["antiplatelet"], rxcui="1191", otc=True, spec=ALL, uses=["heart attack prevention", "stent", "pain"], aliases=["asa", "baby aspirin", "low-dose aspirin", "acetylsalicylic acid"])
drug("clopidogrel", ["Plavix"], ["antiplatelet", "p2y12"], boxed=True, rxcui="32968", spec=FM + CARD)
drug("prasugrel", ["Effient"], ["antiplatelet", "p2y12"], boxed=True, rxcui="613391", spec=CARD)
drug("ticagrelor", ["Brilinta"], ["antiplatelet", "p2y12"], boxed=True, rxcui="1116632", spec=CARD)
drug("cilostazol", ["Pletal"], ["antiplatelet_other"], boxed=True, spec=CARD)

# ---------------------------------------------------------------- NSAIDs and analgesics
drug("ibuprofen", ["Advil", "Motrin"], ["nsaid"], boxed=True, rxcui="5640", otc=True, spec=ALL, uses=["pain", "arthritis", "inflammation", "fever"])
drug("naproxen", ["Aleve", "Naprosyn", "Anaprox"], ["nsaid"], boxed=True, rxcui="7258", otc=True, spec=ALL, aliases=["naproxen sodium"])
drug("meloxicam", ["Mobic"], ["nsaid"], boxed=True, rxcui="41493", spec=FM)
drug("celecoxib", ["Celebrex"], ["nsaid", "cox2", "nonantibiotic_sulfonamide"], boxed=True, rxcui="140587", spec=FM, sa=[["citalopram", "Celebrex / Celexa"]], uses=["arthritis", "pain", "inflammation"])
drug("diclofenac", ["Voltaren", "Cambia", "Zipsor"], ["nsaid"], boxed=True, rxcui="3355", spec=FM)
drug("ketorolac", ["Toradol"], ["nsaid"], boxed=True, rxcui="35827", spec=FM, sa=[["tramadol", "Toradol / tramadol"]], uses=["pain"])
drug("indomethacin", ["Indocin"], ["nsaid"], boxed=True, rxcui="5781", spec=FM)
drug("etodolac", [], ["nsaid"], boxed=True, spec=FM)
drug("nabumetone", [], ["nsaid"], boxed=True, spec=FM)
drug("acetaminophen", ["Tylenol"], ["analgesic_other"], rxcui="161", otc=True, spec=ALL, aliases=["paracetamol", "apap"])

# ---------------------------------------------------------------- opioids and related
drug("oxycodone", ["OxyContin", "Roxicodone"], ["opioid"], hr="opioid", sched="CII", boxed=True, rxcui="7804", spec=FM, sa=[["hydrocodone-acetaminophen", "oxyCODONE / HYDROcodone"]])
drug("oxycodone-acetaminophen", ["Percocet"], ["opioid", "analgesic_other"], hr="opioid", sched="CII", boxed=True, spec=FM, ingredients=["oxycodone", "acetaminophen"], aliases=["oxycodone/acetaminophen", "oxycodone and acetaminophen"])
drug("hydrocodone-acetaminophen", ["Norco", "Vicodin", "Lortab"], ["opioid", "analgesic_other"], hr="opioid", sched="CII", boxed=True, spec=FM, ingredients=["hydrocodone", "acetaminophen"], aliases=["hydrocodone/acetaminophen", "hydrocodone and acetaminophen", "hydrocodone"], sa=[["oxycodone", "HYDROcodone / oxyCODONE"]])
drug("morphine", ["MS Contin"], ["opioid"], hr="opioid", sched="CII", boxed=True, rxcui="7052", spec=FM, sa=[["hydromorphone", "morphine / HYDROmorphone"]])
drug("hydromorphone", ["Dilaudid"], ["opioid"], hr="opioid", sched="CII", boxed=True, rxcui="3423", spec=FM, sa=[["morphine", "HYDROmorphone / morphine"]])
drug("fentanyl", ["Duragesic"], ["opioid", "serotonergic"], hr="opioid", sched="CII", boxed=True, rxcui="4337", spec=FM)
drug("methadone", ["Dolophine", "Methadose"], ["opioid", "serotonergic", "qt_prolonging", "qt_high"], hr="opioid", sched="CII", boxed=True, rxcui="6813", spec=FM)
drug("tramadol", ["Ultram"], ["opioid", "serotonergic"], hr="opioid", sched="CIV", boxed=True, rxcui="10689", spec=FM, sa=[["trazodone", "traMADol / traZODone"], ["ketorolac", "traMADol / Toradol"]], uses=["pain"])
drug("tapentadol", ["Nucynta"], ["opioid", "serotonergic"], hr="opioid", sched="CII", boxed=True, spec=FM)
drug("codeine", [], ["opioid"], hr="opioid", sched="CII", boxed=True, rxcui="2670", spec=FM)
drug("acetaminophen-codeine", ["Tylenol with Codeine", "Tylenol #3"], ["opioid", "analgesic_other"], hr="opioid", sched="CIII", boxed=True, spec=FM, ingredients=["acetaminophen", "codeine"], aliases=["tylenol 3", "acetaminophen/codeine"])
drug("buprenorphine", ["Subutex", "Butrans"], ["opioid"], hr="opioid", sched="CIII", boxed=True, rxcui="1819", spec=FM)
drug("buprenorphine-naloxone", ["Suboxone", "Zubsolv"], ["opioid"], hr="opioid", sched="CIII", boxed=True, spec=FM, ingredients=["buprenorphine", "naloxone"])
drug("meperidine", ["Demerol"], ["opioid", "serotonergic"], hr="opioid", sched="CII", boxed=True, spec=FM)
drug("naloxone", ["Narcan"], ["opioid_antagonist"], rxcui="7242", spec=FM)
drug("naltrexone", ["Vivitrol", "Revia"], ["opioid_antagonist"], rxcui="7243", spec=FM)

# ---------------------------------------------------------------- antidepressants / serotonergic
drug("sertraline", ["Zoloft"], ["ssri", "serotonergic"], boxed=True, rxcui="36437", spec=FM, sa=[["cetirizine", "sertraline / cetirizine"]], uses=["depression", "anxiety", "ptsd", "ocd", "panic"])
drug("fluoxetine", ["Prozac"], ["ssri", "serotonergic", "strong_cyp2d6_inhibitor"], boxed=True, rxcui="4493", spec=FM, sa=[["paroxetine", "FLUoxetine / PARoxetine"]], uses=["depression", "anxiety"])
drug("paroxetine", ["Paxil"], ["ssri", "serotonergic", "strong_cyp2d6_inhibitor", "anticholinergic"], boxed=True, rxcui="32937", spec=FM, sa=[["fluoxetine", "PARoxetine / FLUoxetine"]])
drug("citalopram", ["Celexa"], ["ssri", "serotonergic", "qt_prolonging"], boxed=True, rxcui="2556", spec=FM, sa=[["celecoxib", "Celexa / Celebrex"]], uses=["depression", "anxiety"])
drug("escitalopram", ["Lexapro"], ["ssri", "serotonergic", "qt_prolonging"], boxed=True, rxcui="321988", spec=FM)
drug("fluvoxamine", ["Luvox"], ["ssri", "serotonergic", "cyp1a2_strong_inhibitor"], boxed=True, rxcui="42355", spec=FM)
drug("venlafaxine", ["Effexor"], ["snri", "serotonergic"], boxed=True, rxcui="39786", spec=FM)
drug("desvenlafaxine", ["Pristiq"], ["snri", "serotonergic"], boxed=True, spec=FM)
drug("duloxetine", ["Cymbalta"], ["snri", "serotonergic"], boxed=True, rxcui="72625", spec=FM)
drug("bupropion", ["Wellbutrin", "Zyban"], ["strong_cyp2d6_inhibitor", "smoking_cessation"], boxed=True, rxcui="42347", spec=FM, sa=[["buspirone", "buPROPion / busPIRone"]], uses=["depression", "smoking"])
drug("mirtazapine", ["Remeron"], ["serotonergic"], boxed=True, rxcui="15996", spec=FM)
drug("trazodone", ["Desyrel"], ["serotonergic"], boxed=True, rxcui="10737", spec=FM, sa=[["tramadol", "traZODone / traMADol"]], uses=["sleep", "insomnia", "depression"])
drug("buspirone", ["Buspar"], ["serotonergic"], rxcui="1827", spec=FM, sa=[["bupropion", "busPIRone / buPROPion"]])
drug("amitriptyline", ["Elavil"], ["tca", "serotonergic", "anticholinergic"], boxed=True, rxcui="704", spec=FM)
drug("nortriptyline", ["Pamelor"], ["tca", "anticholinergic"], boxed=True, rxcui="7531", spec=FM)
drug("imipramine", ["Tofranil"], ["tca", "serotonergic", "anticholinergic"], boxed=True, spec=FM)
drug("doxepin", ["Silenor"], ["tca", "anticholinergic"], boxed=True, spec=FM)
drug("clomipramine", ["Anafranil"], ["tca", "serotonergic", "anticholinergic"], boxed=True, spec=FM)
drug("phenelzine", ["Nardil"], ["maoi"], boxed=True, rxcui="8123", spec=FM)
drug("tranylcypromine", ["Parnate"], ["maoi"], boxed=True, spec=FM)
drug("isocarboxazid", ["Marplan"], ["maoi"], boxed=True, spec=FM)
drug("selegiline", ["Emsam", "Zelapar"], ["maoi"], rxcui="9639", spec=FM)
drug("rasagiline", ["Azilect"], ["maoi"], spec=FM)
drug("linezolid", ["Zyvox"], ["maoi_like", "oxazolidinone"], rxcui="190376", spec=FM)
drug("methylene blue", ["ProvayBlue"], ["maoi_like"], spec=FM)
drug("lithium", ["Lithobid"], ["lithium_class", "serotonergic"], hr="narrow therapeutic index", boxed=True, rxcui="6448", spec=FM, aliases=["lithium carbonate"])
drug("dextromethorphan", ["Delsym", "Robitussin DM"], ["serotonergic", "antitussive"], otc=True, rxcui="3289", spec=FM)
drug("st johns wort", ["St. John's wort"], ["serotonergic", "strong_cyp3a4_inducer"], otc=True, spec=FM, aliases=["st. john's wort", "saint john's wort", "hypericum"])

# ---------------------------------------------------------------- triptans / ergots / migraine
drug("sumatriptan", ["Imitrex"], ["triptan", "serotonergic"], rxcui="37418", spec=FM, sa=[["sitagliptin", "SUMAtriptan / SITagliptin"]], uses=["migraine", "headache"])
drug("rizatriptan", ["Maxalt"], ["triptan", "serotonergic"], spec=FM)
drug("zolmitriptan", ["Zomig"], ["triptan", "serotonergic"], spec=FM)
drug("eletriptan", ["Relpax"], ["triptan", "serotonergic"], spec=FM)
drug("ergotamine", ["Cafergot"], ["ergot"], boxed=True, spec=FM)
drug("dihydroergotamine", ["D.H.E. 45", "Migranal"], ["ergot"], boxed=True, spec=FM)

# ---------------------------------------------------------------- sedatives / hypnotics / relaxants
drug("alprazolam", ["Xanax"], ["benzodiazepine"], sched="CIV", boxed=True, rxcui="596", spec=FM, sa=[["lorazepam", "ALPRAZolam / LORazepam"]])
drug("lorazepam", ["Ativan"], ["benzodiazepine"], sched="CIV", boxed=True, rxcui="6470", spec=FM, sa=[["alprazolam", "LORazepam / ALPRAZolam"], ["clonazepam", "LORazepam / clonazePAM"]])
drug("clonazepam", ["Klonopin"], ["benzodiazepine"], sched="CIV", boxed=True, rxcui="2598", spec=FM, sa=[["clonidine", "Klonopin / clonidine"], ["lorazepam", "clonazePAM / LORazepam"]], uses=["anxiety", "panic", "seizure"])
drug("diazepam", ["Valium"], ["benzodiazepine"], sched="CIV", boxed=True, rxcui="3322", spec=FM)
drug("temazepam", ["Restoril"], ["benzodiazepine"], sched="CIV", boxed=True, spec=FM)
drug("chlordiazepoxide", ["Librium"], ["benzodiazepine"], sched="CIV", boxed=True, spec=FM)
drug("zolpidem", ["Ambien"], ["z_drug"], sched="CIV", boxed=True, rxcui="39993", spec=FM, uses=["sleep", "insomnia"])
drug("eszopiclone", ["Lunesta"], ["z_drug"], sched="CIV", boxed=True, spec=FM)
drug("ramelteon", ["Rozerem"], [], spec=FM)
drug("suvorexant", ["Belsomra"], [], sched="CIV", spec=FM)
drug("cyclobenzaprine", ["Flexeril"], ["muscle_relaxant", "anticholinergic"], rxcui="21949", spec=FM)
drug("methocarbamol", ["Robaxin"], ["muscle_relaxant"], rxcui="6845", spec=FM)
drug("carisoprodol", ["Soma"], ["muscle_relaxant"], sched="CIV", rxcui="2101", spec=FM)
drug("tizanidine", ["Zanaflex"], ["muscle_relaxant"], rxcui="57258", spec=FM, sa=[["tiagabine", "tiZANidine / tiaGABine"]])
drug("baclofen", ["Lioresal"], ["muscle_relaxant"], rxcui="1292", spec=FM)
drug("gabapentin", ["Neurontin", "Gralise"], ["gabapentinoid", "antiepileptic"], rxcui="25480", spec=FM, uses=["nerve pain", "neuropathy", "seizure", "shingles pain"])
drug("pregabalin", ["Lyrica"], ["gabapentinoid", "antiepileptic"], sched="CV", rxcui="187832", spec=FM)

# ---------------------------------------------------------------- antihistamines / antiemetics
drug("diphenhydramine", ["Benadryl", "ZzzQuil", "Unisom SleepGels"], ["first_gen_antihistamine", "anticholinergic", "antihistamine_any"], otc=True, rxcui="3498", spec=FM, uses=["allergy", "sleep", "itch"])
drug("acetaminophen-diphenhydramine", ["Tylenol PM"], ["first_gen_antihistamine", "anticholinergic", "analgesic_other"], otc=True, spec=FM, ingredients=["acetaminophen", "diphenhydramine"], aliases=["acetaminophen/diphenhydramine"])
drug("ibuprofen-diphenhydramine", ["Advil PM", "Motrin PM"], ["nsaid", "first_gen_antihistamine", "anticholinergic"], otc=True, boxed=True, spec=FM, ingredients=["ibuprofen", "diphenhydramine"], aliases=["ibuprofen/diphenhydramine"])
drug("hydroxyzine", ["Atarax", "Vistaril"], ["first_gen_antihistamine", "anticholinergic", "antihistamine_any", "qt_prolonging"], rxcui="5553", spec=FM, sa=[["hydralazine", "hydrOXYzine / hydrALAZINE"]], uses=["itch", "itching", "pruritus", "hives", "urticaria", "anxiety", "allergy", "sleep"])
drug("chlorpheniramine", ["Chlor-Trimeton"], ["first_gen_antihistamine", "anticholinergic", "antihistamine_any"], otc=True, rxcui="2400", spec=FM)
drug("promethazine", ["Phenergan"], ["first_gen_antihistamine", "anticholinergic", "antihistamine_any", "antiemetic"], boxed=True, rxcui="8745", spec=FM)
drug("doxylamine", ["Unisom SleepTabs"], ["first_gen_antihistamine", "anticholinergic", "antihistamine_any"], otc=True, rxcui="3642", spec=FM)
drug("meclizine", ["Antivert", "Bonine"], ["first_gen_antihistamine", "anticholinergic", "antihistamine_any"], rxcui="6694", spec=FM)
drug("cetirizine", ["Zyrtec"], ["second_gen_antihistamine", "antihistamine_any"], otc=True, rxcui="20610", spec=FM, sa=[["sertraline", "cetirizine / sertraline"], ["olanzapine", "Zyrtec / Zyprexa"]])
drug("loratadine", ["Claritin"], ["second_gen_antihistamine", "antihistamine_any"], otc=True, rxcui="28889", spec=FM)
drug("fexofenadine", ["Allegra"], ["second_gen_antihistamine", "antihistamine_any"], otc=True, rxcui="87636", spec=FM)
drug("ondansetron", ["Zofran"], ["antiemetic", "qt_prolonging"], rxcui="26225", spec=FM)
drug("metoclopramide", ["Reglan"], ["antiemetic"], boxed=True, rxcui="6915", spec=FM)
drug("prochlorperazine", ["Compazine"], ["antiemetic", "antipsychotic"], boxed=True, spec=FM)

# ---------------------------------------------------------------- antipsychotics / stimulants / neuro
drug("haloperidol", ["Haldol"], ["antipsychotic", "qt_prolonging", "qt_high"], boxed=True, rxcui="5093", spec=FM)
drug("quetiapine", ["Seroquel"], ["antipsychotic", "qt_prolonging"], boxed=True, rxcui="51272", spec=FM)
drug("olanzapine", ["Zyprexa"], ["antipsychotic"], boxed=True, rxcui="61381", spec=FM, sa=[["cetirizine", "Zyprexa / Zyrtec"]])
drug("risperidone", ["Risperdal"], ["antipsychotic"], boxed=True, rxcui="35636", spec=FM, sa=[["ropinirole", "risperiDONE / rOPINIRole"]])
drug("aripiprazole", ["Abilify"], ["antipsychotic"], boxed=True, rxcui="89013", spec=FM)
drug("ziprasidone", ["Geodon"], ["antipsychotic", "qt_prolonging", "qt_high"], boxed=True, spec=FM)
drug("pimozide", ["Orap"], ["antipsychotic", "qt_prolonging", "qt_high"], spec=FM)
drug("chlorpromazine", [], ["antipsychotic", "qt_prolonging", "anticholinergic"], boxed=True, spec=FM)
drug("thioridazine", [], ["antipsychotic", "qt_prolonging", "qt_high", "anticholinergic"], boxed=True, spec=FM)
drug("ropinirole", ["Requip"], [], spec=FM, sa=[["risperidone", "rOPINIRole / risperiDONE"]])
drug("amphetamine-dextroamphetamine", ["Adderall"], ["amphetamine_like"], sched="CII", boxed=True, spec=FM, ingredients=["amphetamine", "dextroamphetamine"], aliases=["mixed amphetamine salts", "amphetamine salts"])
drug("dextroamphetamine", ["Dexedrine"], ["amphetamine_like"], sched="CII", boxed=True, rxcui="3288", spec=FM)
drug("lisdexamfetamine", ["Vyvanse"], ["amphetamine_like"], sched="CII", boxed=True, spec=FM)
drug("methylphenidate", ["Ritalin", "Concerta"], ["amphetamine_like"], sched="CII", boxed=True, rxcui="6901", spec=FM)
drug("pseudoephedrine", ["Sudafed"], ["sympathomimetic"], otc=True, spec=FM)
drug("phenylephrine", ["Sudafed PE"], ["sympathomimetic"], otc=True, spec=FM)
drug("donepezil", ["Aricept"], ["dementia_drug", "qt_prolonging"], rxcui="135447", spec=FM)
drug("memantine", ["Namenda"], ["dementia_drug"], rxcui="6719", spec=FM)
drug("carbidopa-levodopa", ["Sinemet", "Rytary"], [], spec=FM, ingredients=["carbidopa", "levodopa"], aliases=["carbidopa/levodopa", "levodopa"])
drug("lamotrigine", ["Lamictal"], ["antiepileptic", "anticonvulsant_sodium"], boxed=True, rxcui="28439", spec=FM, sa=[["terbinafine", "LamICTAL / LamISIL"], ["levetiracetam", "lamoTRIgine / levETIRAcetam"], ["levothyroxine", "lamoTRIgine / levothyroxine"]], uses=["seizure", "epilepsy", "bipolar"])
drug("levetiracetam", ["Keppra"], ["antiepileptic"], rxcui="114477", spec=FM, sa=[["lamotrigine", "levETIRAcetam / lamoTRIgine"]])
drug("topiramate", ["Topamax"], ["antiepileptic"], rxcui="38404", spec=FM)
drug("valproate", ["Depakote", "Depakene"], ["antiepileptic"], boxed=True, rxcui="11118", spec=FM, aliases=["valproic acid", "divalproex", "divalproex sodium"])
drug("carbamazepine", ["Tegretol"], ["antiepileptic", "anticonvulsant_sodium", "strong_cyp3a4_inducer"], hr="narrow therapeutic index", boxed=True, rxcui="2002", spec=FM, sa=[["oxcarbazepine", "carBAMazepine / OXcarbazepine"]])
drug("oxcarbazepine", ["Trileptal"], ["antiepileptic", "anticonvulsant_sodium"], spec=FM, sa=[["carbamazepine", "OXcarbazepine / carBAMazepine"]])
drug("phenytoin", ["Dilantin"], ["antiepileptic", "anticonvulsant_sodium", "strong_cyp3a4_inducer"], hr="narrow therapeutic index", rxcui="8183", spec=FM)
drug("tiagabine", ["Gabitril"], ["antiepileptic"], spec=FM, sa=[["tizanidine", "tiaGABine / tiZANidine"]])

# ---------------------------------------------------------------- RAAS / BP / diuretics
drug("lisinopril", ["Prinivil", "Zestril", "Qbrelis"], ["acei"], boxed=True, rxcui="29046", spec=ALL, uses=["blood pressure", "hypertension", "heart failure", "kidney protection"])
drug("enalapril", ["Vasotec"], ["acei"], boxed=True, rxcui="3827", spec=FM + CARD)
drug("ramipril", ["Altace"], ["acei"], boxed=True, rxcui="35296", spec=FM + CARD)
drug("benazepril", ["Lotensin"], ["acei"], boxed=True, rxcui="18867", spec=FM + CARD)
drug("captopril", [], ["acei"], boxed=True, spec=CARD)
drug("quinapril", ["Accupril"], ["acei"], boxed=True, spec=FM + CARD)
drug("losartan", ["Cozaar"], ["arb"], boxed=True, rxcui="52175", spec=ALL, uses=["blood pressure", "hypertension"])
drug("valsartan", ["Diovan"], ["arb"], boxed=True, rxcui="69749", spec=FM + CARD)
drug("irbesartan", ["Avapro"], ["arb"], boxed=True, rxcui="83818", spec=FM + CARD)
drug("olmesartan", ["Benicar"], ["arb"], boxed=True, rxcui="321064", spec=FM + CARD)
drug("telmisartan", ["Micardis"], ["arb"], boxed=True, rxcui="73494", spec=FM + CARD)
drug("candesartan", ["Atacand"], ["arb"], boxed=True, spec=FM + CARD)
drug("sacubitril-valsartan", ["Entresto"], ["arni"], boxed=True, spec=CARD, ingredients=["sacubitril", "valsartan"], aliases=["sacubitril/valsartan"])
drug("aliskiren", ["Tekturna"], ["renin_inhibitor"], boxed=True, rxcui="325646", spec=CARD)
drug("spironolactone", ["Aldactone", "CaroSpir"], ["k_sparing"], rxcui="9997", spec=ALL, uses=["heart failure", "blood pressure", "edema", "aldosterone"])
drug("eplerenone", ["Inspra"], ["k_sparing"], rxcui="298869", spec=CARD)
drug("finerenone", ["Kerendia"], ["k_sparing"], spec=ENDO + CARD)
drug("amiloride", ["Midamor"], ["k_sparing"], rxcui="644", spec=CARD, sa=[["amlodipine", "aMILoride / amLODIPine"]])
drug("triamterene-hydrochlorothiazide", ["Maxzide", "Dyazide"], ["k_sparing", "thiazide", "nonantibiotic_sulfonamide"], spec=FM, ingredients=["triamterene", "hydrochlorothiazide"], aliases=["triamterene/hctz", "triamterene"])
drug("potassium chloride", ["Klor-Con", "K-Tab", "Micro-K"], ["potassium_supplement"], rxcui="8591", spec=ALL, aliases=["kcl", "potassium", "potassium supplement"])
drug("potassium citrate", ["Urocit-K"], ["potassium_supplement"], spec=FM)
drug("hydrochlorothiazide", ["Microzide"], ["thiazide", "nonantibiotic_sulfonamide"], rxcui="5487", spec=ALL, aliases=["hctz"])
drug("chlorthalidone", ["Thalitone"], ["thiazide", "nonantibiotic_sulfonamide"], rxcui="2409", spec=FM + CARD)
drug("indapamide", [], ["thiazide", "nonantibiotic_sulfonamide"], spec=FM + CARD)
drug("metolazone", [], ["thiazide", "nonantibiotic_sulfonamide"], spec=CARD)
drug("furosemide", ["Lasix"], ["loop", "nonantibiotic_sulfonamide"], boxed=True, rxcui="4603", spec=ALL, uses=["edema", "heart failure", "fluid"])
drug("bumetanide", ["Bumex"], ["loop", "nonantibiotic_sulfonamide"], boxed=True, spec=CARD)
drug("torsemide", ["Soaanz", "Demadex"], ["loop", "nonantibiotic_sulfonamide"], spec=CARD)
drug("metoprolol", ["Lopressor", "Toprol XL"], ["beta_blocker"], rxcui="6918", spec=ALL, aliases=["metoprolol tartrate", "metoprolol succinate"], uses=["blood pressure", "heart rate", "afib", "heart failure", "angina"])
drug("atenolol", ["Tenormin"], ["beta_blocker"], boxed=True, rxcui="1202", spec=FM + CARD)
drug("carvedilol", ["Coreg"], ["beta_blocker", "beta_blocker_nonselective"], rxcui="20352", spec=FM + CARD)
drug("propranolol", ["Inderal", "InnoPran"], ["beta_blocker", "beta_blocker_nonselective"], boxed=True, rxcui="8787", spec=FM + CARD)
drug("nadolol", ["Corgard"], ["beta_blocker", "beta_blocker_nonselective"], boxed=True, rxcui="7226", spec=CARD)
drug("bisoprolol", ["Zebeta"], ["beta_blocker"], spec=FM + CARD)
drug("nebivolol", ["Bystolic"], ["beta_blocker"], spec=FM + CARD)
drug("labetalol", ["Trandate"], ["beta_blocker", "beta_blocker_nonselective"], spec=FM + CARD)
drug("amlodipine", ["Norvasc"], ["ccb_dhp"], rxcui="17767", spec=ALL, sa=[["amiloride", "amLODIPine / aMILoride"]], uses=["blood pressure", "hypertension", "angina"])
drug("nifedipine", ["Procardia", "Adalat CC"], ["ccb_dhp"], spec=FM + CARD, sa=[["nicardipine", "NIFEdipine / niCARdipine"]])
drug("felodipine", [], ["ccb_dhp"], spec=CARD)
drug("nicardipine", ["Cardene"], ["ccb_dhp"], spec=CARD, sa=[["nifedipine", "niCARdipine / NIFEdipine"]])
drug("diltiazem", ["Cardizem", "Cartia XT"], ["ccb_nondhp", "moderate_cyp3a4_inhibitor"], rxcui="3443", spec=FM + CARD)
drug("verapamil", ["Calan", "Verelan"], ["ccb_nondhp", "moderate_cyp3a4_inhibitor", "pgp_inhibitor"], rxcui="11170", spec=FM + CARD)
drug("hydralazine", ["Apresoline"], ["vasodilator"], rxcui="5470", spec=FM + CARD, sa=[["hydroxyzine", "hydrALAZINE / hydrOXYzine"]], uses=["blood pressure", "hypertension", "heart failure"])
drug("clonidine", ["Catapres", "Kapvay"], ["central_alpha_agonist"], rxcui="2599", spec=FM + CARD, sa=[["clonazepam", "cloNIDine / Klonopin"]], uses=["blood pressure", "hypertension", "adhd", "withdrawal"])
drug("doxazosin", ["Cardura"], ["alpha_blocker"], rxcui="49276", spec=FM)
drug("terazosin", [], ["alpha_blocker"], rxcui="37798", spec=FM)
drug("tamsulosin", ["Flomax"], ["alpha_blocker"], rxcui="77492", spec=FM)
drug("prazosin", ["Minipress"], ["alpha_blocker"], rxcui="8629", spec=FM)
drug("minoxidil", ["Loniten"], ["vasodilator"], boxed=True, rxcui="6984", spec=CARD)

# ---------------------------------------------------------------- lipids / cardiac
drug("atorvastatin", ["Lipitor"], ["statin"], rxcui="83367", spec=ALL, uses=["cholesterol", "lipids", "ldl"])
drug("simvastatin", ["Zocor"], ["statin"], rxcui="36567", spec=ALL, uses=["cholesterol", "lipids"])
drug("rosuvastatin", ["Crestor"], ["statin"], rxcui="301542", spec=ALL)
drug("pravastatin", ["Pravachol"], ["statin"], rxcui="42463", spec=FM + CARD)
drug("lovastatin", ["Altoprev"], ["statin"], rxcui="6472", spec=FM)
drug("pitavastatin", ["Livalo"], ["statin"], spec=CARD)
drug("ezetimibe", ["Zetia"], ["lipid_other"], rxcui="341248", spec=FM + CARD)
drug("gemfibrozil", ["Lopid"], ["fibrate"], rxcui="4719", spec=FM + CARD)
drug("fenofibrate", ["Tricor", "Trilipix"], ["fibrate"], rxcui="8703", spec=FM + CARD)
drug("nitroglycerin", ["Nitrostat", "Nitro-Dur"], ["nitrate"], rxcui="4917", spec=FM + CARD, aliases=["ntg", "nitro"])
drug("isosorbide mononitrate", ["Imdur"], ["nitrate"], spec=FM + CARD, aliases=["isosorbide"])
drug("isosorbide dinitrate", ["Isordil"], ["nitrate"], spec=CARD)
drug("ranolazine", ["Ranexa"], ["antianginal", "qt_prolonging"], spec=CARD)
drug("amiodarone", ["Pacerone"], ["antiarrhythmic", "qt_prolonging", "qt_high", "moderate_cyp3a4_inhibitor", "pgp_inhibitor", "cyp1a2_strong_inhibitor"], hr="antiarrhythmic", boxed=True, rxcui="703", spec=CARD)
drug("dronedarone", ["Multaq"], ["antiarrhythmic", "qt_prolonging", "qt_high"], hr="antiarrhythmic", boxed=True, spec=CARD)
drug("dofetilide", ["Tikosyn"], ["antiarrhythmic", "qt_prolonging", "qt_high"], hr="antiarrhythmic", boxed=True, rxcui="49247", spec=CARD)
drug("sotalol", ["Betapace", "Sorine"], ["antiarrhythmic", "beta_blocker", "beta_blocker_nonselective", "qt_prolonging", "qt_high"], hr="antiarrhythmic", boxed=True, rxcui="9947", spec=CARD)
drug("flecainide", ["Tambocor"], ["antiarrhythmic"], hr="antiarrhythmic", boxed=True, rxcui="4441", spec=CARD)
drug("propafenone", ["Rythmol"], ["antiarrhythmic"], hr="antiarrhythmic", boxed=True, spec=CARD)
drug("digoxin", ["Lanoxin"], ["cardiac_glycoside"], hr="narrow therapeutic index", rxcui="3407", spec=CARD + FM)
drug("sildenafil", ["Viagra", "Revatio"], ["pde5"], rxcui="136411", spec=FM + PULM)
drug("tadalafil", ["Cialis", "Adcirca"], ["pde5"], rxcui="358263", spec=FM)
drug("vardenafil", ["Levitra"], ["pde5", "qt_prolonging"], spec=FM)
drug("riociguat", ["Adempas"], [], boxed=True, spec=PULM + CARD)

# ---------------------------------------------------------------- diabetes / endocrine
drug("metformin", ["Glucophage", "Glumetza", "Fortamet"], ["biguanide"], boxed=True, rxcui="6809", spec=ALL, sa=[["metronidazole", "metFORMIN / metroNIDAZOLE"]], uses=["diabetes", "blood sugar", "glucose", "a1c", "prediabetes", "pcos"])
drug("glipizide", ["Glucotrol"], ["sulfonylurea"], hr="hypoglycemia (sulfonylurea)", rxcui="4821", spec=FM + ENDO, sa=[["glyburide", "glipiZIDE / glyBURIDE"]])
drug("glyburide", ["Glynase", "Diabeta"], ["sulfonylurea", "long_acting_sulfonylurea"], hr="hypoglycemia (sulfonylurea)", rxcui="4815", spec=FM + ENDO, sa=[["glipizide", "glyBURIDE / glipiZIDE"]], aliases=["glibenclamide"])
drug("glimepiride", ["Amaryl"], ["sulfonylurea", "long_acting_sulfonylurea"], hr="hypoglycemia (sulfonylurea)", rxcui="25789", spec=FM + ENDO)
drug("pioglitazone", ["Actos"], ["tzd"], boxed=True, rxcui="33738", spec=FM + ENDO)
drug("rosiglitazone", ["Avandia"], ["tzd"], boxed=True, spec=ENDO)
drug("sitagliptin", ["Januvia", "Zituvio"], ["dpp4"], rxcui="593411", spec=FM + ENDO, sa=[["sumatriptan", "SITagliptin / SUMAtriptan"]])
drug("linagliptin", ["Tradjenta"], ["dpp4"], spec=FM + ENDO)
drug("saxagliptin", ["Onglyza"], ["dpp4"], spec=ENDO)
drug("empagliflozin", ["Jardiance"], ["sglt2"], rxcui="1545653", spec=ALL, uses=["diabetes", "heart failure", "kidney"])
drug("dapagliflozin", ["Farxiga"], ["sglt2"], rxcui="1488564", spec=ALL)
drug("canagliflozin", ["Invokana"], ["sglt2"], rxcui="1373458", spec=ENDO)
drug("ertugliflozin", ["Steglatro"], ["sglt2"], spec=ENDO)
drug("semaglutide", ["Ozempic", "Wegovy", "Rybelsus"], ["glp1"], boxed=True, rxcui="1991302", spec=FM + ENDO + CARD)
drug("liraglutide", ["Victoza", "Saxenda"], ["glp1"], boxed=True, rxcui="475968", spec=ENDO)
drug("dulaglutide", ["Trulicity"], ["glp1"], boxed=True, spec=FM + ENDO)
drug("tirzepatide", ["Mounjaro", "Zepbound"], ["glp1"], boxed=True, spec=FM + ENDO)
drug("exenatide", ["Byetta", "Bydureon BCise"], ["glp1"], spec=ENDO)
drug("insulin glargine", ["Lantus", "Basaglar", "Toujeo", "Semglee", "Rezvoglar"], ["insulin"], hr="insulin", rxcui="274783", spec=FM + ENDO, aliases=["glargine", "long-acting insulin", "basal insulin"])
drug("insulin detemir", ["Levemir"], ["insulin"], hr="insulin", rxcui="139825", spec=ENDO, aliases=["detemir"])
drug("insulin degludec", ["Tresiba"], ["insulin"], hr="insulin", rxcui="1670007", spec=ENDO, aliases=["degludec"])
drug("insulin lispro", ["Humalog", "Admelog", "Lyumjev"], ["insulin"], hr="insulin", rxcui="86009", spec=FM + ENDO, aliases=["lispro"], sa=[["insulin regular", "HumaLOG / HumuLIN"]])
drug("insulin aspart", ["NovoLog", "Fiasp"], ["insulin"], hr="insulin", rxcui="51428", spec=FM + ENDO, aliases=["aspart"], sa=[["insulin regular", "NovoLOG / NovoLIN"]])
drug("insulin regular", ["Humulin R", "Novolin R"], ["insulin"], hr="insulin", spec=ENDO, aliases=["regular insulin", "insulin human regular"], sa=[["insulin lispro", "HumuLIN / HumaLOG"], ["insulin aspart", "NovoLIN / NovoLOG"]])
drug("insulin nph", ["Humulin N", "Novolin N"], ["insulin"], hr="insulin", spec=ENDO, aliases=["nph insulin", "insulin isophane", "nph"])
drug("levothyroxine", ["Synthroid", "Levoxyl", "Unithroid", "Euthyrox"], ["thyroid"], boxed=True, rxcui="10582", spec=FM + ENDO, sa=[["lamotrigine", "levothyroxine / lamoTRIgine"]], uses=["thyroid", "hypothyroidism"])
drug("liothyronine", ["Cytomel"], ["thyroid"], boxed=True, spec=ENDO)
drug("methimazole", ["Tapazole"], [], spec=ENDO)
drug("propylthiouracil", [], [], boxed=True, spec=ENDO)
drug("alendronate", ["Fosamax"], ["bisphosphonate"], rxcui="46041", spec=FM + ENDO)
drug("risedronate", ["Actonel"], ["bisphosphonate"], spec=ENDO)
drug("testosterone", ["AndroGel", "Testim", "Depo-Testosterone"], ["androgen"], sched="CIII", boxed=True, rxcui="10379", spec=ENDO)
drug("estradiol", ["Estrace", "Vivelle-Dot", "Climara"], ["estrogen"], boxed=True, rxcui="4083", spec=FM + ENDO)
drug("ethinyl estradiol-norethindrone", ["Loestrin", "Junel"], ["estrogen"], boxed=True, spec=FM, ingredients=["ethinyl estradiol", "norethindrone"], aliases=["birth control pill", "combined oral contraceptive"])
drug("medroxyprogesterone", ["Provera", "Depo-Provera"], ["hormone_other"], boxed=True, spec=FM)
drug("norgestimate-ethinyl estradiol", ["Sprintec", "Tri-Sprintec", "Ortho Tri-Cyclen"], ["estrogen"], boxed=True, spec=FM, ingredients=["norgestimate", "ethinyl estradiol"], aliases=["norgestimate/ethinyl estradiol", "norgestimate and ethinyl estradiol"])
drug("levonorgestrel-ethinyl estradiol", ["Aviane", "Seasonique", "Levora"], ["estrogen"], boxed=True, spec=FM, ingredients=["levonorgestrel", "ethinyl estradiol"], aliases=["levonorgestrel/ethinyl estradiol"])
drug("drospirenone-ethinyl estradiol", ["Yaz", "Yasmin"], ["estrogen"], boxed=True, spec=FM, ingredients=["drospirenone", "ethinyl estradiol"], aliases=["drospirenone/ethinyl estradiol"])
drug("norethindrone", ["Camila", "Errin", "Aygestin"], ["hormone_other"], spec=FM, aliases=["progestin-only pill", "mini-pill"])
drug("tamoxifen", ["Soltamox"], ["serm"], boxed=True, rxcui="10324", spec=FM)
drug("prednisone", ["Rayos"], ["corticosteroid"], rxcui="8640", spec=ALL, sa=[["prednisolone", "predniSONE / prednisoLONE"]])
drug("prednisolone", ["Orapred"], ["corticosteroid"], spec=FM, sa=[["prednisone", "prednisoLONE / predniSONE"]])
drug("methylprednisolone", ["Medrol"], ["corticosteroid"], rxcui="6902", spec=FM + PULM)
drug("dexamethasone", ["Decadron"], ["corticosteroid"], rxcui="3264", spec=FM)

# ---------------------------------------------------------------- GI
drug("omeprazole", ["Prilosec"], ["ppi"], rxcui="7646", otc=True, spec=ALL)
drug("esomeprazole", ["Nexium"], ["ppi"], rxcui="283742", otc=True, spec=FM)
drug("pantoprazole", ["Protonix"], ["ppi"], rxcui="40790", spec=ALL)
drug("lansoprazole", ["Prevacid"], ["ppi"], otc=True, spec=FM)
drug("rabeprazole", ["Aciphex"], ["ppi"], spec=FM)
drug("dexlansoprazole", ["Dexilant"], ["ppi"], spec=FM)
drug("famotidine", ["Pepcid"], ["h2ra"], rxcui="4278", otc=True, spec=FM)
drug("cimetidine", ["Tagamet"], ["h2ra"], otc=True, spec=FM)
drug("sucralfate", ["Carafate"], ["polyvalent_cation"], rxcui="10156", spec=FM)
drug("calcium carbonate", ["Tums", "Os-Cal", "Caltrate"], ["polyvalent_cation", "vitamin"], rxcui="1897", otc=True, spec=FM, aliases=["calcium"])
drug("ferrous sulfate", ["Feosol", "Slow Fe"], ["polyvalent_cation", "vitamin"], rxcui="24947", otc=True, spec=FM, aliases=["iron", "iron supplement"])
drug("magnesium oxide", ["Mag-Ox"], ["polyvalent_cation", "vitamin"], otc=True, spec=FM, aliases=["magnesium"])
drug("aluminum hydroxide-magnesium hydroxide", ["Maalox", "Mylanta"], ["polyvalent_cation"], otc=True, spec=FM, aliases=["antacid", "maalox"])
drug("loperamide", ["Imodium"], ["antidiarrheal"], rxcui="6468", otc=True, spec=FM)
drug("polyethylene glycol 3350", ["MiraLAX"], ["laxative"], otc=True, spec=FM, aliases=["miralax", "peg 3350", "polyethylene glycol"])
drug("docusate", ["Colace"], ["laxative"], otc=True, spec=FM)
drug("senna", ["Senokot"], ["laxative"], otc=True, spec=FM, aliases=["sennosides"])

# ---------------------------------------------------------------- anti-infectives
drug("amoxicillin", ["Amoxil"], ["penicillin"], rxcui="723", spec=FM, uses=["infection", "strep", "sinusitis", "ear infection", "pneumonia"])
drug("amoxicillin-clavulanate", ["Augmentin"], ["penicillin"], spec=FM, ingredients=["amoxicillin", "clavulanate"], aliases=["amoxicillin/clavulanate", "amox-clav", "augmentin"])
drug("ampicillin", [], ["penicillin"], spec=FM)
drug("penicillin v", ["Penicillin VK"], ["penicillin"], spec=FM, aliases=["penicillin vk", "pen vk", "penicillin v potassium", "penicillin"])
drug("penicillin g benzathine", ["Bicillin L-A"], ["penicillin"], spec=FM, aliases=["bicillin", "penicillin g"])
drug("dicloxacillin", [], ["penicillin"], spec=FM)
drug("piperacillin-tazobactam", ["Zosyn"], ["penicillin"], spec=FM, ingredients=["piperacillin", "tazobactam"])
drug("cephalexin", ["Keflex"], ["cephalosporin"], rxcui="2231", spec=FM, uses=["infection", "skin infection", "cellulitis", "uti"])
drug("cefadroxil", [], ["cephalosporin"], spec=FM)
drug("cefuroxime", ["Ceftin"], ["cephalosporin"], rxcui="2194", spec=FM)
drug("cefdinir", ["Omnicef"], ["cephalosporin"], rxcui="25037", spec=FM)
drug("cefpodoxime", ["Vantin"], ["cephalosporin"], spec=FM)
drug("ceftriaxone", ["Rocephin"], ["cephalosporin"], rxcui="2193", spec=FM)
drug("cefazolin", ["Ancef"], ["cephalosporin"], spec=FM)
drug("azithromycin", ["Zithromax", "Z-Pak"], ["macrolide", "qt_prolonging"], rxcui="18631", spec=FM + PULM, aliases=["z-pack", "zpack", "z pak"])
drug("clarithromycin", ["Biaxin"], ["macrolide", "qt_prolonging", "strong_cyp3a4_inhibitor", "pgp_inhibitor"], rxcui="21212", spec=FM + PULM)
drug("erythromycin", ["Ery-Tab", "EryPed"], ["macrolide", "qt_prolonging", "moderate_cyp3a4_inhibitor"], rxcui="4053", spec=FM)
drug("ciprofloxacin", ["Cipro"], ["fluoroquinolone", "qt_prolonging", "cyp1a2_strong_inhibitor"], boxed=True, rxcui="2551", spec=FM)
drug("levofloxacin", ["Levaquin"], ["fluoroquinolone", "qt_prolonging"], boxed=True, rxcui="82122", spec=FM + PULM)
drug("moxifloxacin", ["Avelox"], ["fluoroquinolone", "qt_prolonging"], boxed=True, rxcui="139462", spec=FM + PULM)
drug("doxycycline", ["Vibramycin", "Doryx"], ["tetracycline_class"], rxcui="3640", spec=FM + PULM)
drug("minocycline", ["Minocin", "Solodyn"], ["tetracycline_class"], rxcui="6980", spec=FM)
drug("tetracycline", [], ["tetracycline_class"], rxcui="10395", spec=FM)
drug("sulfamethoxazole-trimethoprim", ["Bactrim", "Septra"], ["sulfonamide_antibiotic"], spec=FM, ingredients=["sulfamethoxazole", "trimethoprim"], aliases=["tmp-smx", "smx-tmp", "bactrim ds", "sulfamethoxazole/trimethoprim", "co-trimoxazole", "trimethoprim-sulfamethoxazole", "trimethoprim/sulfamethoxazole"])
drug("trimethoprim", ["Primsol"], [], rxcui="10829", spec=FM)
drug("nitrofurantoin", ["Macrobid", "Macrodantin"], ["nitrofuran"], rxcui="7454", spec=FM)
drug("metronidazole", ["Flagyl"], [], rxcui="6922", spec=FM, sa=[["metformin", "metroNIDAZOLE / metFORMIN"]], uses=["infection", "bacterial vaginosis", "c diff", "trichomonas"])
drug("clindamycin", ["Cleocin"], ["lincosamide"], boxed=True, rxcui="2582", spec=FM)
drug("vancomycin", ["Vancocin", "Firvanq"], ["glycopeptide"], rxcui="11124", spec=FM)
drug("fluconazole", ["Diflucan"], ["azole_antifungal", "qt_prolonging", "moderate_cyp3a4_inhibitor"], rxcui="4450", spec=FM)
drug("itraconazole", ["Sporanox"], ["azole_antifungal", "strong_cyp3a4_inhibitor", "pgp_inhibitor"], boxed=True, rxcui="28031", spec=FM + PULM)
drug("ketoconazole", [], ["azole_antifungal", "strong_cyp3a4_inhibitor", "pgp_inhibitor"], boxed=True, rxcui="6135", spec=FM)
drug("voriconazole", ["Vfend"], ["azole_antifungal", "strong_cyp3a4_inhibitor", "qt_prolonging"], spec=PULM)
drug("posaconazole", ["Noxafil"], ["azole_antifungal", "strong_cyp3a4_inhibitor", "qt_prolonging"], spec=PULM)
drug("terbinafine", ["Lamisil"], [], rxcui="37801", spec=FM, sa=[["lamotrigine", "LamISIL / LamICTAL"]], uses=["fungal", "nail fungus", "athlete's foot", "ringworm"])
drug("valacyclovir", ["Valtrex"], ["antiviral"], rxcui="73645", spec=FM)
drug("acyclovir", ["Zovirax"], ["antiviral"], rxcui="281", spec=FM)
drug("oseltamivir", ["Tamiflu"], ["antiviral"], rxcui="260101", spec=FM + PULM)
drug("nirmatrelvir-ritonavir", ["Paxlovid"], ["antiviral", "protease_inhibitor", "strong_cyp3a4_inhibitor"], spec=FM + PULM, ingredients=["nirmatrelvir", "ritonavir"], aliases=["paxlovid", "nirmatrelvir/ritonavir"])
drug("ritonavir", ["Norvir"], ["antiviral", "protease_inhibitor", "strong_cyp3a4_inhibitor", "pgp_inhibitor"], rxcui="85762", spec=FM)
drug("atazanavir", ["Reyataz"], ["antiviral", "protease_inhibitor"], spec=FM)
drug("darunavir", ["Prezista"], ["antiviral", "protease_inhibitor"], spec=FM)
drug("rifampin", ["Rifadin"], ["strong_cyp3a4_inducer"], rxcui="9384", spec=FM + PULM, aliases=["rifampicin"])
drug("isoniazid", [], [], rxcui="6038", spec=FM + PULM)
drug("hydroxychloroquine", ["Plaquenil"], ["antimalarial", "dmard", "qt_prolonging"], rxcui="5521", spec=FM)

# ---------------------------------------------------------------- respiratory
drug("albuterol", ["ProAir", "Ventolin", "Proventil"], ["saba"], rxcui="435", spec=FM + PULM, aliases=["salbutamol", "rescue inhaler"])
drug("levalbuterol", ["Xopenex"], ["saba"], spec=PULM)
drug("ipratropium", ["Atrovent"], ["lama"], rxcui="7213", spec=PULM)
drug("ipratropium-albuterol", ["Combivent Respimat", "DuoNeb"], ["saba", "lama"], spec=PULM, ingredients=["ipratropium", "albuterol"], aliases=["duoneb", "combivent"])
drug("tiotropium", ["Spiriva"], ["lama"], rxcui="69120", spec=PULM + FM)
drug("umeclidinium-vilanterol", ["Anoro Ellipta"], ["lama", "laba"], spec=PULM, ingredients=["umeclidinium", "vilanterol"])
drug("fluticasone-salmeterol", ["Advair", "Wixela", "AirDuo"], ["inhaled_corticosteroid", "laba"], spec=PULM + FM, ingredients=["fluticasone", "salmeterol"], aliases=["advair diskus", "fluticasone/salmeterol"])
drug("budesonide-formoterol", ["Symbicort", "Breyna"], ["inhaled_corticosteroid", "laba"], spec=PULM + FM, ingredients=["budesonide", "formoterol"], aliases=["budesonide/formoterol"])
drug("fluticasone-vilanterol", ["Breo Ellipta"], ["inhaled_corticosteroid", "laba"], spec=PULM, ingredients=["fluticasone", "vilanterol"])
drug("fluticasone-umeclidinium-vilanterol", ["Trelegy Ellipta"], ["inhaled_corticosteroid", "laba", "lama"], spec=PULM, ingredients=["fluticasone", "umeclidinium", "vilanterol"], aliases=["trelegy"])
drug("fluticasone", ["Flovent", "Flonase", "Arnuity"], ["inhaled_corticosteroid"], rxcui="41126", spec=FM + PULM)
drug("budesonide", ["Pulmicort", "Entocort"], ["inhaled_corticosteroid"], rxcui="19831", spec=PULM)
drug("mometasone", ["Asmanex", "Nasonex"], ["inhaled_corticosteroid"], spec=PULM)
drug("montelukast", ["Singulair"], ["leukotriene"], boxed=True, rxcui="88249", spec=FM + PULM)
drug("roflumilast", ["Daliresp"], ["pde4"], spec=PULM)
drug("benzonatate", ["Tessalon"], ["antitussive"], spec=FM + PULM)
drug("guaifenesin", ["Mucinex"], ["expectorant"], rxcui="5032", otc=True, spec=FM + PULM)
drug("theophylline", ["Theo-24", "Elixophyllin"], [], hr="narrow therapeutic index", spec=PULM)
drug("varenicline", ["Chantix"], ["smoking_cessation"], rxcui="591622", spec=FM + PULM)
drug("nicotine", ["Nicoderm CQ", "Nicorette"], ["smoking_cessation"], rxcui="7407", otc=True, spec=FM + PULM, aliases=["nicotine patch", "nicotine gum"])

# ---------------------------------------------------------------- gout / rheum / transplant / onc / misc
drug("allopurinol", ["Zyloprim"], ["xanthine_oxidase_inhibitor", "gout_other"], rxcui="519", spec=FM)
drug("febuxostat", ["Uloric"], ["xanthine_oxidase_inhibitor", "gout_other"], boxed=True, spec=FM)
drug("colchicine", ["Colcrys", "Mitigare"], ["gout_other"], rxcui="2683", spec=FM + CARD)
drug("methotrexate", ["Trexall", "Otrexup", "Rasuvo"], ["dmard", "immunosuppressant"], hr="methotrexate (weekly dosing)", boxed=True, rxcui="6851", spec=FM)
drug("azathioprine", ["Imuran"], ["thiopurine", "immunosuppressant"], hr="immunosuppressant", boxed=True, rxcui="1256", spec=FM)
drug("mercaptopurine", ["Purixan"], ["thiopurine", "immunosuppressant"], hr="immunosuppressant", rxcui="103", spec=FM)
drug("cyclosporine", ["Neoral", "Gengraf", "Sandimmune"], ["immunosuppressant", "pgp_inhibitor"], hr="immunosuppressant", boxed=True, rxcui="3008", spec=FM)
drug("tacrolimus", ["Prograf", "Envarsus XR"], ["immunosuppressant"], hr="immunosuppressant", boxed=True, rxcui="42316", spec=FM)
drug("mycophenolate", ["CellCept", "Myfortic"], ["immunosuppressant"], hr="immunosuppressant", boxed=True, spec=FM, aliases=["mycophenolate mofetil"])
drug("irinotecan", ["Camptosar"], ["chemotherapy"], hr="chemotherapy", boxed=True, spec=FM)
drug("procarbazine", ["Matulane"], ["chemotherapy", "maoi_like"], hr="chemotherapy", boxed=True, spec=FM)
drug("oxybutynin", ["Ditropan XL"], ["urinary_antimuscarinic", "anticholinergic"], rxcui="32675", spec=FM)
drug("tolterodine", ["Detrol"], ["urinary_antimuscarinic", "anticholinergic"], spec=FM)
drug("finasteride", ["Proscar", "Propecia"], ["five_ari"], rxcui="25025", spec=FM)
drug("cyanocobalamin", ["Nascobal"], ["vitamin"], rxcui="11248", otc=True, spec=FM, aliases=["vitamin b12", "b12"])
drug("folic acid", [], ["vitamin"], rxcui="4511", otc=True, spec=FM)
drug("cholecalciferol", ["Vitamin D3"], ["vitamin"], rxcui="2418", otc=True, spec=FM, aliases=["vitamin d", "vitamin d3"])

# ---------------------------------------------------------------- build
by_generic = {}
for e in D:
    g = e["generic"]
    assert g not in by_generic, f"duplicate {g}"
    for c in e["classes"]:
        assert c in CLASSES, f"{g}: unknown class {c}"
    by_generic[g] = e

for e in D:
    for partner, why in e.get("sa", []):
        assert partner in by_generic, f"{e['generic']}: unknown sound-alike partner {partner}"

drugs_out = {}
for e in D:
    drugs_out[e["generic"]] = {
        "brands": e["brands"],
        "aliases": e.get("aliases", []),
        "classes": e["classes"],
        "ingredients": e.get("ingredients", [e["generic"]]),
        "high_risk": e.get("hr"),
        "schedule": e.get("sched"),
        "boxed_warning": bool(e.get("boxed", False)),
        "otc": bool(e.get("otc", False)),
        "rxcui": e.get("rxcui"),
        "specialties": e.get("spec", FM),
        "sound_alike": [{"drug": p, "pair": why} for p, why in e.get("sa", [])],
        "uses": e.get("uses", []),
    }

classes_out = {cid: {"label": label, "duplicate": dup} for cid, (label, dup) in CLASSES.items()}

(OUT / "drugs.json").write_text(json.dumps({
    "version": "2026-11-03",
    "note": "Curated for the CustomAi MedScribe demo. Generic names, US brands, classes, federal DEA schedules, boxed-warning presence and ISMP-style confused-name pairs. RxCUIs only where well established; others resolve via RxNav at runtime.",
    "drugs": drugs_out,
}, indent=1, ensure_ascii=False) + "\n")
(OUT / "classes.json").write_text(json.dumps({"version": "2026-11-03", "classes": classes_out}, indent=1, ensure_ascii=False) + "\n")
print(len(drugs_out), "drugs,", len(classes_out), "classes")
