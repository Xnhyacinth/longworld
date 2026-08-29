from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from longworld.core.provenance import ProvenanceError
from longworld.core.wikiparse import (
    WIKI_CHURCHILL_LATE_QUOTE,
    WIKI_CHURCHILL_MID_QUOTE,
    WIKI_COMMEMORATION_QUOTE,
    WIKI_EINSTEIN_LATE_QUOTE,
    WIKI_EINSTEIN_MID_QUOTE,
    WIKI_ELIZABETH_LATE_QUOTE,
    WIKI_ELIZABETH_MID_QUOTE,
    WIKI_JEFFERSON_LATE_QUOTE,
    WIKI_JEFFERSON_MID_QUOTE,
    WIKI_MLK_LATE_QUOTE,
    WIKI_MLK_MID_QUOTE,
    WIKI_NEWTON_LATE_QUOTE,
    WIKI_NEWTON_MID_QUOTE,
    WIKI_OBAMA_LATE_QUOTE,
    WIKI_OBAMA_MID_QUOTE,
    WIKI_POPULAR_CULTURE_QUOTE,
    WIKI_THATCHER_LATE_QUOTE,
    WIKI_THATCHER_MID_QUOTE,
    WIKI_TURING_LATE_QUOTE,
    WIKI_TURING_MID_QUOTE,
    parse_wiki_claim_program,
)

ADA_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p5_ada_smoke"
)
P7_DIRECTORY = (
    Path(__file__).parents[1]
    / "data"
    / "source_inventory"
    / "wikimedia_p7_source_rich_v1"
)
CHURCHILL_DIRECTORY = (
    Path(__file__).parents[1]
    / "data"
    / "source_inventory"
    / "wikimedia_p7_churchill_v1"
)
EINSTEIN_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_einstein_v1"
)
THATCHER_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_thatcher_v1"
)
NEWTON_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_newton_v1"
)
OBAMA_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_obama_v1"
)
ELIZABETH_DIRECTORY = (
    Path(__file__).parents[1]
    / "data"
    / "source_inventory"
    / "wikimedia_p7_elizabeth_ii_v1"
)
MLK_DIRECTORY = (
    Path(__file__).parents[1] / "data" / "source_inventory" / "wikimedia_p7_mlk_v1"
)
JEFFERSON_DIRECTORY = (
    Path(__file__).parents[1]
    / "data"
    / "source_inventory"
    / "wikimedia_p7_jefferson_v1"
)


def _load(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8")
    return text, hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture(scope="module")
def ada_program():
    wikipedia, wiki_hash = _load(ADA_DIRECTORY / "wikipedia-974-r1370153024.json")
    entity, entity_hash = _load(ADA_DIRECTORY / "wikidata-Q7259-r2531694935.json")
    return parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )


def test_ada_program_uses_non_overlapping_body_facts(ada_program) -> None:
    by_id = {section.section_id: section for section in ada_program.sections}
    assert ada_program.entity_id == "Q7259"
    assert ada_program.title == "Ada Lovelace"
    assert set(by_id) == {
        "early_work",
        "commemoration",
        "popular_culture",
        "wikidata_entity",
    }
    born = by_id["early_work"].facts[0]
    assert born.role == "born"
    assert born.value == "1815-12-10"
    assert born.evidence_quote == "{{birth date|df=y|1815|12|10}}"
    assert WIKI_COMMEMORATION_QUOTE not in by_id["early_work"].wikitext
    assert WIKI_POPULAR_CULTURE_QUOTE not in by_id["early_work"].wikitext
    assert WIKI_POPULAR_CULTURE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_COMMEMORATION_QUOTE
    assert by_id["popular_culture"].facts[0].value == WIKI_POPULAR_CULTURE_QUOTE
    assert by_id["wikidata_entity"].facts[0].value == "Q7259"
    reconstructed = (
        by_id["early_work"].wikitext
        + by_id["commemoration"].wikitext
        + by_id["popular_culture"].wikitext
    )
    wikipedia, _ = _load(ADA_DIRECTORY / "wikipedia-974-r1370153024.json")
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    assert reconstructed == wikitext
    assert not hasattr(born, "text")


def test_ineligible_wikipedia_pages_fail_closed() -> None:
    wikipedia, wiki_hash = _load(P7_DIRECTORY / "wikipedia-12590-r1369191214.json")
    entity, entity_hash = _load(P7_DIRECTORY / "wikidata-Q11641-r2517790987.json")
    with pytest.raises(ProvenanceError, match="heading"):
        parse_wiki_claim_program(
            wikipedia_text=wikipedia,
            entity_text=entity,
            wikipedia_parent_hash=wiki_hash,
            entity_parent_hash=entity_hash,
        )


def test_turing_program_uses_non_overlapping_body_facts() -> None:
    wikipedia, wiki_hash = _load(P7_DIRECTORY / "wikipedia-1208-r1369298323.json")
    entity, entity_hash = _load(P7_DIRECTORY / "wikidata-Q7251-r2533461519.json")
    program = parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )
    by_id = {section.section_id: section for section in program.sections}
    assert program.entity_id == "Q7251"
    assert program.title == "Alan Turing"
    born = by_id["early_work"].facts[0]
    assert born.role == "born"
    assert born.value == "1912-06-23"
    assert born.evidence_quote == "{{Birth date|df=y|1912|6|23}}"
    assert WIKI_TURING_MID_QUOTE not in by_id["early_work"].wikitext
    assert WIKI_TURING_LATE_QUOTE not in by_id["early_work"].wikitext
    assert WIKI_TURING_LATE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_TURING_MID_QUOTE
    assert by_id["popular_culture"].facts[0].value == WIKI_TURING_LATE_QUOTE
    assert by_id["wikidata_entity"].facts[0].value == "Q7251"
    reconstructed = (
        by_id["early_work"].wikitext
        + by_id["commemoration"].wikitext
        + by_id["popular_culture"].wikitext
    )
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    assert reconstructed == wikitext
    assert by_id["early_work"].ground_value in by_id["early_work"].wikitext
    assert by_id["commemoration"].ground_value in by_id["commemoration"].wikitext
    assert by_id["popular_culture"].ground_value in by_id["popular_culture"].wikitext


def test_churchill_program_uses_non_overlapping_body_facts() -> None:
    wikipedia, wiki_hash = _load(
        CHURCHILL_DIRECTORY / "wikipedia-33265-r1367982973.json"
    )
    entity, entity_hash = _load(CHURCHILL_DIRECTORY / "wikidata-Q8016-r2535896268.json")
    program = parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )
    by_id = {section.section_id: section for section in program.sections}
    assert program.entity_id == "Q8016"
    assert program.title == "Winston Churchill"
    born = by_id["early_work"].facts[0]
    assert born.value == "1874-11-30"
    assert born.evidence_quote == "{{birth date|1874|11|30|df=y}}"
    assert WIKI_CHURCHILL_MID_QUOTE not in by_id["early_work"].wikitext
    assert WIKI_CHURCHILL_LATE_QUOTE not in by_id["early_work"].wikitext
    assert WIKI_CHURCHILL_LATE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_CHURCHILL_MID_QUOTE
    assert by_id["popular_culture"].facts[0].value == WIKI_CHURCHILL_LATE_QUOTE
    reconstructed = (
        by_id["early_work"].wikitext
        + by_id["commemoration"].wikitext
        + by_id["popular_culture"].wikitext
    )
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    assert reconstructed == wikitext


def test_einstein_program_uses_non_overlapping_body_facts() -> None:
    wikipedia, wiki_hash = _load(EINSTEIN_DIRECTORY / "wikipedia-736-r1370002284.json")
    entity, entity_hash = _load(EINSTEIN_DIRECTORY / "wikidata-Q937-r2536235382.json")
    program = parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )
    by_id = {section.section_id: section for section in program.sections}
    assert program.entity_id == "Q937"
    assert program.title == "Albert Einstein"
    early_ids = (
        "early_birth",
        "early_patent",
        "early_academic",
        "early_fame",
        "early_refugee",
    )
    born = by_id["early_birth"].facts[0]
    assert born.value == "1879-03-14"
    assert born.evidence_quote == "{{Birth date|df=yes|1879|3|14}}"
    assert [
        (
            by_id[section_id].facts[0].role,
            by_id[section_id].facts[0].answer_tag,
            by_id[section_id].minimum_tier,
        )
        for section_id in early_ids
    ] == [
        ("born", "BORN", "16k"),
        ("patent_examiner", "PATENT", "16k"),
        ("prague_research", "PRAGUE", "16k"),
        ("scientific_fame", "FAME", "64k"),
        ("refugee_status", "REFUGEE", "16k"),
    ]
    early_text = "".join(by_id[section_id].wikitext for section_id in early_ids)
    assert WIKI_EINSTEIN_MID_QUOTE not in early_text
    assert WIKI_EINSTEIN_LATE_QUOTE not in early_text
    assert WIKI_EINSTEIN_LATE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_EINSTEIN_MID_QUOTE
    assert by_id["popular_culture"].facts[0].value == WIKI_EINSTEIN_LATE_QUOTE
    reconstructed = (
        early_text + by_id["commemoration"].wikitext + by_id["popular_culture"].wikitext
    )
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    tail_start = wikitext.index("== References ==")
    assert reconstructed == wikitext[:tail_start]
    assert reconstructed.count("== References ==") == 0
    assert WIKI_EINSTEIN_LATE_QUOTE in reconstructed
    rest = by_id["appendix_rest"].wikitext
    assert rest.startswith("== References ==")
    assert '<ref name="ILjYQ">' not in rest
    from longworld.core.pack import estimate_tokens

    assert 10_000 <= estimate_tokens(rest) <= 12_000
    assert all(
        by_id[section_id].ground_value in by_id[section_id].wikitext
        for section_id in early_ids
    )
    assert by_id["commemoration"].ground_value in by_id["commemoration"].wikitext
    assert by_id["popular_culture"].ground_value in by_id["popular_culture"].wikitext


def test_thatcher_program_uses_non_overlapping_body_facts() -> None:
    wikipedia, wiki_hash = _load(
        THATCHER_DIRECTORY / "wikipedia-19831-r1370984131.json"
    )
    entity, entity_hash = _load(THATCHER_DIRECTORY / "wikidata-Q7416-r2533606292.json")
    program = parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )
    by_id = {section.section_id: section for section in program.sections}
    assert program.entity_id == "Q7416"
    assert program.title == "Margaret Thatcher"
    born = by_id["early_birth"].facts[0]
    assert born.value == "1925-10-13"
    assert born.evidence_quote == "{{Birth date|df=y|1925|10|13}}"
    early_ids = (
        "early_birth",
        "early_school",
        "early_oxford",
        "early_politics",
        "early_opposition",
    )
    assert [
        (by_id[section_id].facts[0].role, by_id[section_id].facts[0].answer_tag)
        for section_id in early_ids
    ] == [
        ("born", "BORN"),
        ("schooling", "SCHOOL"),
        ("oxford_studies", "OXFORD"),
        ("early_politics", "POLITICS"),
        ("leadership_election", "LEADERSHIP"),
    ]
    early_text = "".join(by_id[section_id].wikitext for section_id in early_ids)
    assert WIKI_THATCHER_MID_QUOTE not in early_text
    assert WIKI_THATCHER_LATE_QUOTE not in early_text
    assert WIKI_THATCHER_LATE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_THATCHER_MID_QUOTE
    assert by_id["commemoration"].facts[0].role == "opposition_speech"
    assert by_id["commemoration"].facts[0].answer_tag == "OPPOSITION"
    assert by_id["popular_culture"].facts[0].value == WIKI_THATCHER_LATE_QUOTE
    assert by_id["popular_culture"].facts[0].role == "westland_affair"
    assert by_id["popular_culture"].facts[0].answer_tag == "WESTLAND"
    reconstructed = (
        early_text + by_id["commemoration"].wikitext + by_id["popular_culture"].wikitext
    )
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    tail_start = wikitext.index("Speaking in Scotland in 2009")
    assert reconstructed == wikitext[:tail_start]
    assert reconstructed.count("==Legacy==") == 1
    assert WIKI_THATCHER_LATE_QUOTE in reconstructed
    rest = by_id["appendix_rest"].wikitext
    assert rest.startswith("Speaking in Scotland in 2009")
    assert "[[Scottish independence]]" not in rest
    from longworld.core.pack import estimate_tokens

    assert 2_000 <= estimate_tokens(rest) <= 3_000
    assert all(
        by_id[section_id].ground_value in by_id[section_id].wikitext
        for section_id in early_ids
    )
    assert by_id["commemoration"].ground_value in by_id["commemoration"].wikitext
    assert by_id["popular_culture"].ground_value in by_id["popular_culture"].wikitext
    assert by_id["appendix_rest"].ground_value.startswith(
        "Speaking in Scotland in 2009"
    )


def test_newton_program_uses_unique_birth_wrapper_and_non_overlapping_body() -> None:
    wikipedia, wiki_hash = _load(NEWTON_DIRECTORY / "wikipedia-14627-r1371274988.json")
    entity, entity_hash = _load(NEWTON_DIRECTORY / "wikidata-Q935-r2533455490.json")
    program = parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )
    by_id = {section.section_id: section for section in program.sections}
    assert program.entity_id == "Q935"
    assert program.title == "Isaac Newton"
    born = by_id["early_birth"].facts[0]
    assert born.role == "born"
    assert born.value == "1643-01-04"
    assert "{{Birth date|df=y|1643|01|04}}" in born.evidence_quote
    assert born.evidence_quote != "{{Birth date|df=y|1643|01|04}}"
    assert wikipedia.count(born.evidence_quote) == 1
    early_ids = (
        "early_birth",
        "early_school",
        "early_cambridge",
        "early_mathematics",
        "early_optics",
    )
    assert [
        (by_id[section_id].facts[0].role, by_id[section_id].facts[0].answer_tag)
        for section_id in early_ids
    ] == [
        ("born", "BORN"),
        ("schooling", "SCHOOL"),
        ("cambridge_studies", "CAMBRIDGE"),
        ("mathematics", "MATH"),
        ("spectrum_observation", "SPECTRUM"),
    ]
    early_text = "".join(by_id[section_id].wikitext for section_id in early_ids)
    assert WIKI_NEWTON_MID_QUOTE not in early_text
    assert WIKI_NEWTON_LATE_QUOTE not in early_text
    assert WIKI_NEWTON_LATE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_NEWTON_MID_QUOTE
    assert by_id["commemoration"].facts[0].role == "optics_work"
    assert by_id["commemoration"].facts[0].answer_tag == "OPTICS"
    assert by_id["popular_culture"].facts[0].value == WIKI_NEWTON_LATE_QUOTE
    assert by_id["popular_culture"].facts[0].role == "mint_prosecution"
    assert by_id["popular_culture"].facts[0].answer_tag == "MINT"
    reconstructed = (
        early_text + by_id["commemoration"].wikitext + by_id["popular_culture"].wikitext
    )
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    tail_start = wikitext.index("== References ==")
    assert reconstructed == wikitext[:tail_start]
    assert reconstructed.count("== References ==") == 0
    assert WIKI_NEWTON_LATE_QUOTE in reconstructed
    rest = by_id["appendix_rest"].wikitext
    assert rest.startswith("== References ==")
    assert "=== Alchemy further reading ===" not in rest
    from longworld.core.pack import estimate_tokens

    assert 3_200 <= estimate_tokens(rest) <= 3_700
    assert all(
        by_id[section_id].ground_value in by_id[section_id].wikitext
        for section_id in early_ids
    )
    assert by_id["commemoration"].ground_value in by_id["commemoration"].wikitext
    assert by_id["popular_culture"].ground_value in by_id["popular_culture"].wikitext
    assert by_id["appendix_rest"].ground_value == "== References =="


def test_obama_program_uses_unique_birth_and_non_overlapping_body() -> None:
    wikipedia, wiki_hash = _load(OBAMA_DIRECTORY / "wikipedia-534366-r1371416342.json")
    entity, entity_hash = _load(OBAMA_DIRECTORY / "wikidata-Q76-r2536275829.json")
    program = parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )
    by_id = {section.section_id: section for section in program.sections}
    assert program.entity_id == "Q76"
    assert program.title == "Barack Obama"
    early_ids = (
        "early_birth",
        "early_degree",
        "early_organizing",
        "early_law",
        "early_family",
    )
    born = by_id["early_birth"].facts[0]
    assert born.role == "born"
    assert born.value == "1961-08-04"
    assert born.evidence_quote == "{{birth date and age|1961|8|4}}"
    assert wikipedia.count(born.evidence_quote) == 1
    assert [
        (
            by_id[section_id].facts[0].role,
            by_id[section_id].facts[0].answer_tag,
            by_id[section_id].minimum_tier,
        )
        for section_id in early_ids
    ] == [
        ("born", "BORN", "16k"),
        ("college_degree", "DEGREE", "16k"),
        ("community_organizer", "ORGANIZER", "16k"),
        ("law_school", "HARVARD", "32k"),
        ("maternal_grandmother", "GRANDMOTHER", "16k"),
    ]
    early_text = "".join(by_id[section_id].wikitext for section_id in early_ids)
    assert WIKI_OBAMA_MID_QUOTE not in early_text
    assert WIKI_OBAMA_LATE_QUOTE not in early_text
    assert WIKI_OBAMA_LATE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_OBAMA_MID_QUOTE
    assert by_id["popular_culture"].facts[0].value == WIKI_OBAMA_LATE_QUOTE
    assert (
        by_id["popular_culture"].ground_value
        == "===2004 U.S. Senate campaign in Illinois==="
    )
    reconstructed = (
        early_text + by_id["commemoration"].wikitext + by_id["popular_culture"].wikitext
    )
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    tail_start = wikitext.index("====Environmental policy====")
    assert reconstructed == wikitext[:tail_start]
    assert reconstructed.count("====Environmental policy====") == 0
    assert WIKI_OBAMA_LATE_QUOTE in reconstructed
    rest = by_id["appendix_rest"].wikitext
    assert rest.startswith("====Environmental policy====")
    assert "[[The Hill (newspaper)|The Hill]]" not in rest
    from longworld.core.pack import estimate_tokens

    assert 1_100 <= estimate_tokens(rest) <= 1_300
    assert all(
        by_id[section_id].ground_value in by_id[section_id].wikitext
        for section_id in early_ids
    )
    assert by_id["commemoration"].ground_value in by_id["commemoration"].wikitext
    assert by_id["popular_culture"].ground_value in by_id["popular_culture"].wikitext
    assert by_id["appendix_rest"].ground_value == "====Environmental policy===="


def test_elizabeth_program_uses_unique_birth_and_non_overlapping_body() -> None:
    wikipedia, wiki_hash = _load(
        ELIZABETH_DIRECTORY / "wikipedia-12153654-r1371412757.json"
    )
    entity, entity_hash = _load(ELIZABETH_DIRECTORY / "wikidata-Q9682-r2533011239.json")
    program = parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )
    by_id = {section.section_id: section for section in program.sections}
    assert program.entity_id == "Q9682"
    assert program.title == "Elizabeth II"
    born = by_id["early_work"].facts[0]
    assert born.role == "born"
    assert born.value == "1926-04-21"
    assert born.evidence_quote == "{{Birth date|df=yes|1926|04|21}}"
    assert wikipedia.count(born.evidence_quote) == 1
    assert WIKI_ELIZABETH_MID_QUOTE not in by_id["early_work"].wikitext
    assert WIKI_ELIZABETH_LATE_QUOTE not in by_id["early_work"].wikitext
    assert WIKI_ELIZABETH_LATE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_ELIZABETH_MID_QUOTE
    assert by_id["popular_culture"].facts[0].value == WIKI_ELIZABETH_LATE_QUOTE
    reconstructed = (
        by_id["early_work"].wikitext
        + by_id["commemoration"].wikitext
        + by_id["popular_culture"].wikitext
    )
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    tail_start = wikitext.index("==References==")
    assert reconstructed == wikitext[:tail_start]
    assert reconstructed.count("==References==") == 0
    assert WIKI_ELIZABETH_LATE_QUOTE in reconstructed
    rest = by_id["appendix_rest"].wikitext
    assert rest.startswith("==References==")
    assert "==External links==" not in rest
    assert "[[Category:Guinness World Records holders]]" not in rest
    from longworld.core.pack import estimate_tokens

    assert 1_800 <= estimate_tokens(rest) <= 2_200
    assert by_id["early_work"].ground_value in by_id["early_work"].wikitext
    assert by_id["commemoration"].ground_value in by_id["commemoration"].wikitext
    assert by_id["popular_culture"].ground_value in by_id["popular_culture"].wikitext
    assert by_id["appendix_rest"].ground_value == "==References=="


def test_mlk_program_uses_unique_birth_and_non_overlapping_body() -> None:
    wikipedia, wiki_hash = _load(MLK_DIRECTORY / "wikipedia-20076-r1370915588.json")
    entity, entity_hash = _load(MLK_DIRECTORY / "wikidata-Q8027-r2535959791.json")
    program = parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )
    by_id = {section.section_id: section for section in program.sections}
    assert program.entity_id == "Q8027"
    assert program.title == "Martin Luther King Jr."
    born = by_id["early_birth"].facts[0]
    assert born.role == "born"
    assert born.value == "1929-01-15"
    assert born.evidence_quote == "{{birth date|1929|1|15}}"
    assert wikipedia.count(born.evidence_quote) == 1
    early_ids = (
        "early_birth",
        "early_school",
        "early_adolescence",
        "early_morehouse",
        "early_religious",
        "early_family",
        "early_sit_in",
    )
    assert [
        (by_id[section_id].facts[0].role, by_id[section_id].facts[0].answer_tag)
        for section_id in early_ids
    ] == [
        ("born", "BORN"),
        ("schooling", "SCHOOL"),
        ("adolescence_oratory", "SPEECH"),
        ("college_degree", "DEGREE"),
        ("divinity_degree", "DIVINITY"),
        ("marriage_date", "MARRIAGE"),
        ("sit_in_commitment", "SIT_IN"),
    ]
    early_text = "".join(by_id[section_id].wikitext for section_id in early_ids)
    assert WIKI_MLK_MID_QUOTE not in early_text
    assert WIKI_MLK_LATE_QUOTE not in early_text
    assert WIKI_MLK_LATE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_MLK_MID_QUOTE
    assert by_id["commemoration"].facts[0].role == "montgomery_oratory"
    assert by_id["commemoration"].facts[0].answer_tag == "MONTGOMERY"
    assert by_id["popular_culture"].facts[0].value == WIKI_MLK_LATE_QUOTE
    assert by_id["popular_culture"].facts[0].role == "final_sermon"
    assert by_id["popular_culture"].facts[0].answer_tag == "SERMON"
    reconstructed = (
        early_text + by_id["commemoration"].wikitext + by_id["popular_culture"].wikitext
    )
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    tail_start = wikitext.index("=== United States ===")
    assert reconstructed == wikitext[:tail_start]
    assert reconstructed.count("== Legacy ==") == 1
    assert WIKI_MLK_LATE_QUOTE in reconstructed
    rest = by_id["appendix_rest"].wikitext
    assert rest.startswith("=== United States ===")
    assert "==== ''The Measure of a Man'' ====" not in rest
    assert "== Ideas, influences, and political stances ==" in rest
    from longworld.core.pack import estimate_tokens

    assert 4_000 <= estimate_tokens(rest) <= 5_000
    assert all(
        by_id[section_id].ground_value in by_id[section_id].wikitext
        for section_id in early_ids
    )
    assert by_id["commemoration"].ground_value in by_id["commemoration"].wikitext
    assert by_id["popular_culture"].ground_value in by_id["popular_culture"].wikitext
    assert by_id["appendix_rest"].ground_value == "=== United States ==="


def test_jefferson_program_uses_unique_birth_and_non_overlapping_body() -> None:
    wikipedia, wiki_hash = _load(
        JEFFERSON_DIRECTORY / "wikipedia-29922-r1369059101.json"
    )
    entity, entity_hash = _load(
        JEFFERSON_DIRECTORY / "wikidata-Q11812-r2533326601.json"
    )
    program = parse_wiki_claim_program(
        wikipedia_text=wikipedia,
        entity_text=entity,
        wikipedia_parent_hash=wiki_hash,
        entity_parent_hash=entity_hash,
    )
    by_id = {section.section_id: section for section in program.sections}
    assert program.entity_id == "Q11812"
    assert program.title == "Thomas Jefferson"
    born = by_id["early_birth"].facts[0]
    assert born.role == "born"
    assert born.value == "1743-04-13"
    assert born.evidence_quote == "{{birth date|1743|4|13}}"
    assert wikipedia.count(born.evidence_quote) == 1
    early_text = "".join(
        by_id[section_id].wikitext
        for section_id in (
            "early_birth",
            "early_career",
            "early_revolution",
            "early_diplomacy",
        )
    )
    assert WIKI_JEFFERSON_MID_QUOTE not in early_text
    assert WIKI_JEFFERSON_LATE_QUOTE not in early_text
    assert WIKI_JEFFERSON_LATE_QUOTE not in by_id["commemoration"].wikitext
    assert by_id["commemoration"].facts[0].value == WIKI_JEFFERSON_MID_QUOTE
    assert by_id["popular_culture"].facts[0].value == WIKI_JEFFERSON_LATE_QUOTE
    assert [(fact.role, fact.value) for fact in by_id["early_birth"].facts] == [
        ("born", "1743-04-13")
    ]
    assert [(fact.role, fact.value) for fact in by_id["early_career"].facts] == [
        ("early_career", "[[House of Burgesses]]")
    ]
    assert [(fact.role, fact.value) for fact in by_id["early_revolution"].facts] == [
        ("revolutionary_committee", "[[Committee of Five]]")
    ]
    assert [(fact.role, fact.value) for fact in by_id["early_diplomacy"].facts] == [
        ("early_transition", "[[Mather Brown]]")
    ]
    assert by_id["commemoration"].wikitext.startswith("==Secretary of State==")
    assert by_id["popular_culture"].wikitext.startswith("===Autobiography===")
    reconstructed = (
        by_id["early_birth"].wikitext
        + by_id["early_career"].wikitext
        + by_id["early_revolution"].wikitext
        + by_id["early_diplomacy"].wikitext
        + by_id["commemoration"].wikitext
        + by_id["popular_culture"].wikitext
    )
    from longworld.core.wikiparse import extract_wikipedia_wikitext

    _, _, wikitext = extract_wikipedia_wikitext(wikipedia)
    tail_start = wikitext.index("==Legacy==")
    assert reconstructed == wikitext[:tail_start]
    assert reconstructed.count("==Legacy==") == 0
    assert WIKI_JEFFERSON_LATE_QUOTE in reconstructed
    rest = by_id["appendix_rest"].wikitext
    assert rest.startswith("==Legacy==")
    assert "===Thomas Jefferson Foundation sources===" not in rest
    assert "==Writings==" in rest
    from longworld.core.pack import estimate_tokens

    assert 9_500 <= estimate_tokens(rest) <= 10_200
    assert all(
        by_id[section_id].ground_value in by_id[section_id].wikitext
        for section_id in (
            "early_birth",
            "early_career",
            "early_revolution",
            "early_diplomacy",
        )
    )
    assert by_id["commemoration"].ground_value in by_id["commemoration"].wikitext
    assert by_id["popular_culture"].ground_value in by_id["popular_culture"].wikitext
    assert by_id["appendix_rest"].ground_value == "==References=="


def test_plain_text_fixture_is_not_a_claim_program() -> None:
    text = "Ada Lovelace was a mathematician."
    digest = hashlib.sha256(text.encode()).hexdigest()
    with pytest.raises(ProvenanceError):
        parse_wiki_claim_program(
            wikipedia_text=text,
            entity_text=text,
            wikipedia_parent_hash=digest,
            entity_parent_hash=digest,
        )
