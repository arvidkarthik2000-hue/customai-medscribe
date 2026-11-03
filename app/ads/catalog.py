"""Fixed vocabularies for the ad zone. Everything here is fictional.

Brands, generics and companies are invented for the demo; every unit carries
"Sample ad, not a real product". Offices and prescribers are synthetic and are
never given personal names; pseudo-NPIs start with 9, which no real NPI does.
"""
from __future__ import annotations

SPECIALTIES = {
    "family_medicine": "Family medicine",
    "internal_medicine": "Internal medicine",
    "cardiology": "Cardiology",
    "endocrinology": "Endocrinology",
    "pulmonology": "Pulmonology",
}

# Share of the simulated network's offices by specialty.
SPECIALTY_MIX = {
    "family_medicine": 0.34,
    "internal_medicine": 0.26,
    "cardiology": 0.16,
    "endocrinology": 0.12,
    "pulmonology": 0.12,
}

STATES = {
    "NJ": ("New Jersey", ["Newark", "Edison", "Trenton", "Paterson", "Morristown", "Cherry Hill"]),
    "NY": ("New York", ["Brooklyn", "Queens", "Yonkers", "Albany", "Buffalo", "White Plains"]),
    "PA": ("Pennsylvania", ["Philadelphia", "Pittsburgh", "Allentown", "Harrisburg", "Erie"]),
    "CT": ("Connecticut", ["Hartford", "New Haven", "Stamford", "Bridgeport"]),
    "MA": ("Massachusetts", ["Boston", "Worcester", "Springfield", "Lowell"]),
    "MD": ("Maryland", ["Baltimore", "Rockville", "Columbia", "Frederick"]),
    "VA": ("Virginia", ["Richmond", "Norfolk", "Arlington", "Roanoke"]),
    "NC": ("North Carolina", ["Charlotte", "Raleigh", "Durham", "Greensboro"]),
    "FL": ("Florida", ["Miami", "Tampa", "Orlando", "Jacksonville", "Fort Myers"]),
    "GA": ("Georgia", ["Atlanta", "Savannah", "Augusta", "Macon"]),
    "TX": ("Texas", ["Houston", "Dallas", "Austin", "San Antonio", "El Paso"]),
    "IL": ("Illinois", ["Chicago", "Naperville", "Peoria", "Springfield"]),
}

STATE_MIX = {
    "NJ": 0.13, "NY": 0.14, "PA": 0.10, "CT": 0.05, "MA": 0.07, "MD": 0.06,
    "VA": 0.06, "NC": 0.07, "FL": 0.11, "GA": 0.06, "TX": 0.09, "IL": 0.06,
}

# Slots where a sponsored unit may appear. Never on a clinical screen.
SLOTS = {
    "schedule_card": {
        "label": "Schedule card",
        "when": "Beside today's schedule list; closes when a chart opens",
        "formats": ["reminder", "claim"],
        "counted": True,
    },
    "drafting_wait": {
        "label": "Note-drafting wait",
        "when": "The seconds while the note drafts; gone when the draft appears",
        "formats": ["reminder", "claim"],
        "counted": True,
    },
    "post_sign": {
        "label": "Post-sign card",
        "when": "The 'Note complete' screen after Sign",
        "formats": ["affordability", "education"],
        "counted": True,
    },
    "resources_tray": {
        "label": "Resources tray",
        "when": "Opened by the doctor; clinician resources only",
        "formats": ["resource"],
        "counted": False,
    },
}
COUNTED_SLOTS = [s for s, v in SLOTS.items() if v["counted"]]

FORMATS = {
    "reminder": "Reminder card (brand and generic name only)",
    "claim": "Claim card with its most serious risks inside the unit",
    "affordability": "Access and affordability information for the clinician",
    "education": "Disease-education card for the clinician",
    "resource": "Clinician resource (prescribing information, access programme, trial information)",
}

# Ten fictional brands, one per therapeutic category.
BRANDS = [
    {
        "id": "b-corvessa", "name": "Corvessa", "generic": "calvoxitrel", "category": "Lipid management",
        "company": "Orrinvale Therapeutics", "boxed_warning": False, "color": "#2f6f8f", "motif": "rings",
        "indication": "an oral therapy for adults with primary hyperlipidemia (fictional)",
        "claim": "Once-daily oral option studied alongside diet and exercise in adults with high LDL-C (fictional).",
        "risks": "Serious risks may include liver enzyme elevations and muscle pain or weakness. Do not use in patients with active liver disease (fictional).",
    },
    {
        "id": "b-glyvora", "name": "Glyvora", "generic": "sennamivel", "category": "Type 2 diabetes",
        "company": "Quillmoor Pharma", "boxed_warning": False, "color": "#3c7a55", "motif": "waves",
        "indication": "an oral therapy to improve glycemic control in adults with type 2 diabetes (fictional)",
        "claim": "Studied as an add-on to metformin in adults with type 2 diabetes (fictional).",
        "risks": "Serious risks may include low blood sugar when used with insulin or sulfonylureas, and dehydration (fictional).",
    },
    {
        "id": "b-aerilune", "name": "Aerilune", "generic": "tovaprenex", "category": "Asthma and COPD",
        "company": "Sablewood Biosciences", "boxed_warning": False, "color": "#4b6fae", "motif": "arcs",
        "indication": "an inhaled maintenance therapy for adults with asthma or COPD (fictional)",
        "claim": "Twice-daily inhaled maintenance option for adults (fictional). Not for acute symptoms.",
        "risks": "Serious risks may include paradoxical bronchospasm and worsening of narrow-angle glaucoma. Not a rescue inhaler (fictional).",
    },
    {
        "id": "b-derevia", "name": "Derevia", "generic": "milquorent", "category": "Atopic dermatitis",
        "company": "Tarnhollow Health", "boxed_warning": False, "color": "#8a5a8f", "motif": "petals",
        "indication": "a topical therapy for mild to moderate atopic dermatitis (fictional)",
        "claim": "Steroid-free topical option for adults with mild to moderate atopic dermatitis (fictional).",
        "risks": "Serious risks may include application-site reactions and skin infections (fictional).",
    },
    {
        "id": "b-nevalto", "name": "Nevalto", "generic": "pexoravin", "category": "Migraine prevention",
        "company": "Wrenfield Laboratories", "boxed_warning": False, "color": "#6a5fa8", "motif": "dots",
        "indication": "a preventive therapy for episodic migraine in adults (fictional)",
        "claim": "Monthly preventive option for adults with episodic migraine (fictional).",
        "risks": "Serious risks may include hypersensitivity reactions and constipation with serious complications (fictional).",
    },
    {
        "id": "b-cardelion", "name": "Cardelion", "generic": "rovestanor", "category": "Heart failure",
        "company": "Orrinvale Therapeutics", "boxed_warning": False, "color": "#a0473f", "motif": "pulse",
        "indication": "an oral therapy for adults with chronic heart failure with reduced ejection fraction (fictional)",
        "claim": "Studied in adults with chronic HFrEF on guideline-directed therapy (fictional).",
        "risks": "Serious risks may include low blood pressure, worsening kidney function and high potassium (fictional).",
    },
    {
        "id": "b-thromvia", "name": "Thromvia", "generic": "dalquerin", "category": "Anticoagulation",
        "company": "Quillmoor Pharma", "boxed_warning": True, "color": "#7b4a2f", "motif": "lattice",
        "indication": "an oral anticoagulant for adults with non-valvular atrial fibrillation (fictional)",
        "claim": "Oral anticoagulant studied in adults with non-valvular atrial fibrillation (fictional).",
        "risks": "BOXED WARNING (fictional): premature discontinuation increases the risk of thrombotic events; spinal or epidural hematoma with neuraxial anesthesia. Serious risks include bleeding.",
    },
    {
        "id": "b-leantris", "name": "Leantris", "generic": "vomarelix", "category": "Weight management",
        "company": "Sablewood Biosciences", "boxed_warning": True, "color": "#2f7d7a", "motif": "steps",
        "indication": "a weekly injection for chronic weight management in adults (fictional)",
        "claim": "Weekly injection studied with reduced-calorie diet and physical activity (fictional).",
        "risks": "BOXED WARNING (fictional): risk of thyroid C-cell tumors. Serious risks include pancreatitis and gallbladder problems.",
    },
    {
        "id": "b-vaxcendra", "name": "Vaxcendra", "generic": "adult respiratory vaccine (fictional)", "category": "Adult immunization",
        "company": "Wrenfield Laboratories", "boxed_warning": False, "color": "#3f6b9a", "motif": "shield",
        "indication": "a vaccine for adults 60 and older (fictional)",
        "claim": "Single-dose vaccine studied in adults 60 and older (fictional).",
        "risks": "Serious risks may include severe allergic reactions. Most common: injection-site pain and fatigue (fictional).",
    },
    {
        "id": "b-quenvara", "name": "Quenvara", "generic": "quenvotan", "category": "Hypertension",
        "company": "Tarnhollow Health", "boxed_warning": False, "color": "#556b2f", "motif": "bars",
        "indication": "an oral therapy for adults with hypertension (fictional)",
        "claim": "Once-daily oral option for adults with hypertension (fictional).",
        "risks": "Serious risks may include low blood pressure and kidney problems. Do not use in pregnancy (fictional).",
    },
]
BRAND_BY_ID = {b["id"]: b for b in BRANDS}

TEST_OFFICE_ID = "O-DEMO-001"
SAMPLE_LABEL = "Sample ad, not a real product"
SPONSORED_LABEL = "Sponsored"
WHY_TEXT = (
    "Chosen only by your practice's declared specialty and state and by this slot. "
    "Never based on anything said in a visit, on any note or on any patient information."
)

# Assumptions used by the inventory forecast. Shown on screen next to every forecast.
FORECAST_RENDER_RATE = 0.9   # share of capped slot moments where the doctor reaches the screen
WEEKS_PER_MONTH = 52 / 12


def specialty_label(code: str) -> str:
    return SPECIALTIES.get(code, code.replace("_", " ").capitalize())
