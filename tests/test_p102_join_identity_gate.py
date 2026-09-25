"""Identity witnesses must distinguish equal names in different places."""

from longworld.synthesis import wiki_row_binding
from longworld.synthesis.shared_semantic_world import Document, SemanticWorld
from scripts.p102_join_identity_gate import _place_compatible, identity


def test_different_cities_in_same_country_are_not_one_identity() -> None:
    first = (
        "## Facilities\nName | City | Kind\n"
        "Aster Plant | Lučani, Serbia | Alpha\n"
        "Birch Plant | Belgrade, Serbia | Beta\n"
    )
    second = (
        "## Facilities\nName | City | Output\n"
        "Aster Plant | Kruševac, Serbia | 101\n"
        "Cedar Plant | Niš, Serbia | 202\n"
        "Dogwood Plant | Belgrade, Serbia | 303\n"
    )
    world = SemanticWorld(
        (
            Document("d1", "First facilities", first),
            Document("d2", "Second facilities", second),
        ),
        (),
        (),
    )
    tasks = wiki_row_binding.build_join_tasks(world, max_tasks=10)
    assert tasks
    assert all(
        identity(task)["reason"] == "conflicting_auxiliary_identity" for task in tasks
    )


def test_detailed_place_may_match_full_broader_place_only() -> None:
    assert _place_compatible("Stadsholmen, Stockholm", "Stockholm")
    assert not _place_compatible("Lučani, Serbia", "Kruševac, Serbia")
    assert not _place_compatible(
        "coord|33|09|48|N|115|37|00|W|name=Salton Sea",
        "coord|33|09|48|N|115|37|00|W|name=Imperial Valley",
    )
    assert not _place_compatible(
        "coord|33|09|48|N|115|37|00|W|name=Salton Sea",
        "coord|33|09|49|N|115|37|00|W|name=Imperial Valley",
    )
