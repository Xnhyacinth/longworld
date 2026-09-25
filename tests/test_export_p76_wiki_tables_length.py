"""Keep a valid shorter Wiki pair view when complete pages cannot reach 64K."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import export_p76_wiki_tables as tables


@pytest.mark.parametrize("all_page_tokens", [60_000, 132_000])
def test_pair_scope_retains_32k_when_64k_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, all_page_tokens: int
) -> None:
    world = SimpleNamespace(
        documents=[
            SimpleNamespace(doc_id=name, title=name, text="source page")
            for name in ("required_a", "optional", "required_b")
        ]
    )
    task = SimpleNamespace(
        scope=SimpleNamespace(documents=("required_a", "required_b"))
    )

    def scoped(_task: object, doc_ids: tuple[str, ...]) -> SimpleNamespace:
        return SimpleNamespace(scope=SimpleNamespace(documents=doc_ids))

    def compiled(_world: object, view: SimpleNamespace, **_kwargs: object) -> tuple:
        tokens = all_page_tokens if "optional" in view.scope.documents else 50_000
        return None, {"full_chat_tokens": tokens}, None

    monkeypatch.setattr(tables.wiki_table_tasks, "with_scope_documents", scoped)
    monkeypatch.setattr(tables.reader_view, "compile_task", compiled)

    scopes, unsupported = tables._selected_docs(world, task, tokenizer=object())
    assert scopes == {"32k": ("required_a", "required_b")}
    assert "64k" in unsupported
