# P112 Wiki link shortcut screen

The P111 symbolic link-deletion replay was too narrow for reader-text
necessity: four final questions can match their list row label directly to
the target article title or an obvious title prefix. Their answer may remain
recoverable after the link markup is removed, even though `_solve()` refuses
to follow a row without an explicit wikilink.

The pinned P112 screen compares each primary row label with the title and
body of its frozen linked article in the **final reader**. It rejects an
exact/contained title alias or a visible row-label mention in that article.
The source/native/target manifests and all final reader rows are hash-bound;
the output keeps original task IDs and reports every decision. On five P111
tasks, it rejects four `row_label_identifies_target_title` shortcuts and
retains one `Ariljača` → `Harilaq Fortress` task (28,289 full-chat tokens,
five assistant-supervised tokens, all-mask 1/1). The retained case still has
only a bounded lexical shortcut screen, not a guarantee against every
possible semantic inference. The old five-row reader pack is a diagnostic
and must not be promoted unchanged.

```bash
cat data/candidates/p112_wiki_link_strict_unified_v1/manifest.json
less -R data/candidates/p112_wiki_link_strict_unified_v1/decisions.jsonl
cat data/candidates/p112_wiki_link_strict_mask_v1/manifest.json
UV_LINK_MODE=copy uv run --offline python scripts/p112_wiki_link_shortcut_gate.py --source-dir data/candidates/p111_wiki_linked_unified_v2 --native-dir data/candidates/p111_wiki_linked_native_v2 --target-manifest data/candidates/p111_wiki_linked_targets_v1/manifest.json --output data/candidates/p112_wiki_link_strict_unified_v1 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p112_wiki_link_strict_unified_v1 --all --output data/candidates/p112_wiki_link_strict_mask_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p112_wiki_link_shortcut_gate.py
```
