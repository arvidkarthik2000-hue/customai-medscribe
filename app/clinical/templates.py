"""Note templates (section lists) and specialty emphasis."""
from __future__ import annotations

TEMPLATES: dict[str, dict] = {
    "soap": {
        "label": "SOAP note",
        "sections": [
            ("chief_complaint", "Chief complaint"),
            ("hpi", "History of present illness"),
            ("ros", "Review of systems"),
            ("exam", "Physical exam"),
            ("results", "Results and data reviewed"),
            ("assessment", "Assessment"),
            ("plan", "Plan"),
        ],
    },
    "progress": {
        "label": "Progress note",
        "sections": [
            ("interval_history", "Interval history"),
            ("current_status", "Current status and medicines"),
            ("ros", "Review of systems"),
            ("exam", "Exam"),
            ("results", "Data reviewed"),
            ("assessment_plan", "Assessment and plan"),
        ],
    },
    "hp": {
        "label": "History and physical",
        "sections": [
            ("chief_complaint", "Chief complaint"),
            ("hpi", "History of present illness"),
            ("past_history", "Past medical and surgical history"),
            ("medications_reviewed", "Medications reviewed"),
            ("allergies_reviewed", "Allergies reviewed"),
            ("social_history", "Social history"),
            ("family_history", "Family history"),
            ("ros", "Review of systems"),
            ("exam", "Physical exam"),
            ("results", "Results and data reviewed"),
            ("assessment", "Assessment"),
            ("plan", "Plan"),
        ],
    },
    "consult": {
        "label": "Consult note",
        "sections": [
            ("reason", "Reason for consultation"),
            ("hpi", "History of present illness"),
            ("relevant_history", "Relevant history"),
            ("exam", "Physical exam"),
            ("results", "Results and data reviewed"),
            ("impression", "Impression"),
            ("recommendations", "Recommendations"),
        ],
    },
}

SPECIALTIES: dict[str, dict] = {
    "family_medicine": {
        "label": "Family medicine",
        "emphasis": "Cover each problem addressed, medication reconciliation, preventive care and vaccines discussed, and return precautions.",
    },
    "internal_medicine": {
        "label": "Internal medicine",
        "emphasis": "Organise the assessment and plan by problem; capture chronic disease status, medication changes, labs ordered or reviewed, and care coordination.",
    },
    "cardiology": {
        "label": "Cardiology",
        "emphasis": "Capture chest pain, dyspnea, palpitations, syncope, edema and exercise tolerance; NYHA class or risk scores only if stated; anticoagulation and antiplatelet therapy; heart rate, blood pressure, rhythm and device details if mentioned.",
    },
    "endocrinology": {
        "label": "Endocrinology",
        "emphasis": "Capture glucose readings and logs, A1c, hypoglycemia episodes, insulin types, doses and timing, device data if mentioned, thyroid symptoms and labs, and weight.",
    },
    "pulmonology": {
        "label": "Pulmonology",
        "emphasis": "Capture dyspnea, cough, sputum, wheeze, exacerbations, inhaler names and technique, oxygen use, spirometry or imaging if mentioned, and smoking status.",
    },
}

STYLES = {
    "concise": "Concise: short clinical phrases, one fact per item, no filler.",
    "detailed": "Detailed: complete sentences with the relevant context the clinician gave.",
}

ENGINES = ("sonnet", "haiku")


def section_ids(template: str) -> list[str]:
    return [sid for sid, _ in TEMPLATES[template]["sections"]]


def section_title(template: str, section_id: str) -> str:
    for sid, title in TEMPLATES.get(template, TEMPLATES["soap"])["sections"]:
        if sid == section_id:
            return title
    return section_id.replace("_", " ").capitalize()
