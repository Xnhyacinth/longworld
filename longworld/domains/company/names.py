"""Rare name tables — keep surface tokens out of parametric knowledge.

Entities are fictional (not real companies). Uniqueness still matters: filler
worlds must not recycle the same eight project names, or long packing becomes
a cloned haystack.
"""

from __future__ import annotations

import random

FIRST = [
    "Kaelith",
    "Brynno",
    "Orrin",
    "Sable",
    "Quen",
    "Ilyra",
    "Tamsin",
    "Vesper",
    "Neris",
    "Calyx",
    "Rowen",
    "Mirelle",
    "Thalen",
    "Paxine",
    "Sorrel",
    "Edda",
    "Lumen",
    "Wrenna",
    "Halvor",
    "Ysolde",
    "Fenric",
    "Odelia",
    "Rusk",
    "Maelis",
    "Torven",
    "Isolde",
    "Garron",
    "Nyxel",
    "Bramis",
    "Saelith",
]

LAST = [
    "Vos",
    "Helmrick",
    "Durne",
    "Pell",
    "Wex",
    "Sorrelan",
    "Quist",
    "Nerrow",
    "Cindle",
    "Vask",
    "Orrick",
    "Pellune",
    "Warrick",
    "Thornep",
    "Mireck",
    "Ashveil",
    "Crowel",
    "Fenwicke",
    "Glimmer",
    "Harrow",
]

STEMS = [
    "Kestrel",
    "Umber",
    "Nighthawk",
    "Cinder",
    "Pellucid",
    "Vesperine",
    "Calyx",
    "Thorn",
    "Brack",
    "Hollow",
    "Rime",
    "Gilt",
    "Nacre",
    "Sablewood",
    "Quarry",
    "Mirefen",
    "Ashloft",
    "Crowstep",
    "Fenlight",
    "Glimmerock",
]

FORMS = [
    "Systems",
    "Labs",
    "Works",
    "Analytics",
    "Digital",
    "Optics",
    "Forge",
    "Holdings",
    "Partners",
    "Foundry",
]

CUSTOMER_KINDS = [
    "Mutual",
    "Harbor Bank",
    "Freight",
    "Health",
    "Transit",
    "Energy",
    "Logistics",
    "Maritime",
    "Exchange",
    "Reserve",
]

# Kept for tests / old configs that import the lists.
COMPANIES = [f"{s} {f}" for s in STEMS[:8] for f in FORMS[:2]]
CUSTOMERS = [f"{a} {b}" for a in LAST[:8] for b in CUSTOMER_KINDS[:2]]
PROJECTS = list(STEMS)
DEPARTMENTS = [
    "Revenue Ops",
    "Product Delivery",
    "Legal Counsel",
    "Customer Success",
    "Internal Audit",
]


def unique_label(rng: random.Random, used: set[str], prefix: str, n: int = 4) -> str:
    while True:
        token = f"{prefix}-{rng.randint(10 ** (n - 1), 10**n - 1)}"
        if token not in used:
            used.add(token)
            return token


def sample_project_name(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"{rng.choice(STEMS)}{rng.randint(10, 99)}"
        if name not in used:
            used.add(name)
            return name


def sample_company(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"{rng.choice(STEMS)} {rng.choice(FORMS)}"
        if name not in used:
            used.add(name)
            return name


def sample_customer(rng: random.Random, used: set[str]) -> str:
    while True:
        name = f"{rng.choice(LAST)} {rng.choice(CUSTOMER_KINDS)}"
        if name not in used:
            used.add(name)
            return name
