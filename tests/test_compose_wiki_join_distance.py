from longworld.synthesis.wiki_row_binding import JoinTask
from scripts.compose_wiki_join_distance import insert_filler


def test_filler_shifts_only_second_document_evidence():
    task = JoinTask(
        task_id="task",
        question="question",
        answer="answer",
        first_title="A",
        second_title="B",
        name_column="Name",
        selector_column="Selector",
        selector="x",
        target_column="Target",
        first_span=(10, 11),
        bound_name_span=(12, 13),
        target_span=(40, 41),
        first_text_span=(5, 20),
        second_text_span=(30, 50),
        context="a" * 50,
        alternatives=2,
    )

    composed = insert_filler(task, [{"title": "C", "text": "body"}])
    shift = len(composed.context) - len(task.context)

    assert composed.context[:20] == task.context[:20]
    assert composed.context[20 + shift :] == task.context[20:]
    assert composed.first_span == task.first_span
    assert composed.bound_name_span == task.bound_name_span
    assert composed.second_text_span == (30 + shift, 50 + shift)
    assert composed.target_span == (40 + shift, 41 + shift)
