"""Loads the curated knowledge base (app/drugs/kb/*.json) and builds lookup indexes.

Everything here is local and instant; nothing touches the network.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .normalize import norm, strip_dose_and_form, strip_salts

KB_DIR = Path(__file__).resolve().parent / "kb"


def _load(name: str) -> dict:
    return json.loads((KB_DIR / name).read_text(encoding="utf-8"))


@dataclass
class Drug:
    generic: str
    brands: list[str]
    aliases: list[str]
    own_classes: list[str]
    ingredients: list[str]
    high_risk: str | None
    schedule: str | None
    boxed_warning: bool
    otc: bool
    rxcui: str | None
    specialties: list[str]
    sound_alike: list[dict]
    uses: list[str]
    classes: set[str] = field(default_factory=set)  # own classes plus those of known ingredients


class KB:
    def __init__(self) -> None:
        raw = _load("drugs.json")
        self.version = raw["version"]
        self.classes: dict[str, dict] = _load("classes.json")["classes"]
        self.drugs: dict[str, Drug] = {}
        for generic, d in raw["drugs"].items():
            self.drugs[generic] = Drug(
                generic=generic, brands=d["brands"], aliases=d["aliases"], own_classes=d["classes"],
                ingredients=d["ingredients"], high_risk=d["high_risk"], schedule=d["schedule"],
                boxed_warning=d["boxed_warning"], otc=d["otc"], rxcui=d["rxcui"], specialties=d["specialties"],
                sound_alike=d["sound_alike"], uses=d["uses"],
            )
        for drug in self.drugs.values():
            cls = set(drug.own_classes)
            for ing in drug.ingredients:
                if ing != drug.generic and ing in self.drugs:
                    cls |= set(self.drugs[ing].own_classes)
            drug.classes = cls

        # name index: normalized generic / alias / brand -> generic
        self.index: dict[str, str] = {}
        self.brand_index: dict[str, str] = {}
        for drug in self.drugs.values():
            self.index.setdefault(norm(drug.generic), drug.generic)
        for drug in self.drugs.values():
            for alias in drug.aliases:
                self.index.setdefault(norm(alias), drug.generic)
            for brand in drug.brands:
                key = norm(brand)
                self.brand_index.setdefault(key, drug.generic)
                self.index.setdefault(key, drug.generic)
        # spaced variant of combination names: "amoxicillin clavulanate"
        for key, generic in list(self.index.items()):
            spaced = key.replace("-", " ")
            self.index.setdefault(spaced, generic)

        self.interactions = _load("interactions.json")
        self.allergies = _load("allergies.json")
        self.conditions = _load("conditions.json")
        self.doses = _load("doses.json")
        self.renal = _load("renal.json")
        self.older = _load("older_adult.json")
        self.prevention = _load("prevention.json")
        self.formulary = _load("formulary_sample.json")
        self.reviewed = self.interactions["version"]
        self.grading = self.interactions["grading"]

    # ------------------------------------------------------------ lookups
    def lookup(self, name: str | None) -> str | None:
        """Exact match on generic, alias or brand, tolerating dose/form words and salts."""
        if not name:
            return None
        key = norm(name)
        if key in self.index:
            return self.index[key]
        bare = strip_dose_and_form(key)
        if bare in self.index:
            return self.index[bare]
        salt_free = strip_salts(bare)
        if salt_free in self.index:
            return self.index[salt_free]
        return None

    def get(self, generic: str | None) -> Drug | None:
        return self.drugs.get(generic) if generic else None

    def class_label(self, class_id: str) -> str:
        c = self.classes.get(class_id)
        return c["label"] if c else class_id

    def matches(self, selector: dict, drug: Drug) -> bool:
        names = selector.get("drugs") or []
        if names and (drug.generic in names or any(i in names for i in drug.ingredients)):
            return True
        classes = selector.get("classes") or []
        return bool(classes and drug.classes.intersection(classes))

    def names_for(self, generic: str) -> list[str]:
        """All names a label might use for this drug (for label-text searches)."""
        d = self.drugs.get(generic)
        if not d:
            return [generic]
        out = {generic, *d.brands, *(i for i in d.ingredients)}
        return sorted(out, key=len, reverse=True)


@lru_cache(maxsize=1)
def kb() -> KB:
    return KB()
