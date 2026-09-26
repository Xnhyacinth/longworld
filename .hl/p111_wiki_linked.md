# P111 source-backed Wiki list-to-article dependency

P111 tests an automatic real-source relation that the P108 table renderer had
lost: a named row's raw MediaWiki link points to a distinct article, whose
body supplies an attribute absent from the list. The reader sees the original
list with the inline link and the exact-revision target article; it does not
see the hidden support graph or a FACTS index. A selector value in the list
determines which linked article to read. The same question changes answer
when the selector changes to a second supported row.

The bounded source run froze all 171 P108 list pages at exact revisions (157
new raw fetches, 14 reused). The structural scanner found 788 uniquely
visible linked rows on 12 pages from ten source groups. It planned 54 target
articles and froze 50 at a revision no later than the source list; four had
no eligible prior revision. Twenty-two targets had a clean attribute absent
from the list. Twenty-eight same-world two-target/common-field pairs narrowed
to five disjoint final reader tasks from three source worlds: education one,
heritage one and transport three. All are train; no eval claim is made.

The final native and unified readers passed independent replay of source
link, target field, alternate selector/answer, link deletion, field deletion,
text bounds and assistant-only loss mask. The five readers contain 94,267
full-chat tokens and 38 supervised tokens, all below 32K. Their actual
two-evidence span is 1,074–9,823 tokens. A same-world complete-page layout
test produced one extra 39,250-token view of an existing task, with 2,860
token evidence extent and 35,992 tokens from last evidence to query. The
other four tasks lacked safe same-world content to reach 32K; no 64K view
passed. The longer view is a **view, not an independent semantic task**, and
has not been added to the shared candidate index.

The method is narrow: five cases do not establish broad Wiki multi-hop
coverage or a model-learning gain. The target pool and source groups are
versioned; train/eval title and canonical URL collisions are rejected. The
text deletion checks operate within the declared two-article relation and
do not prove a global shortest natural-language proof. `train_ready=false`.

Inspect and replay:

```bash
cat data/candidates/p111_wiki_linked_raw_v1/manifest.json
cat data/candidates/p111_wiki_linked_targets_v1/manifest.json
cat data/candidates/p111_wiki_linked_body_support_v1/ledger.json
cat data/candidates/p111_wiki_linked_native_v2/manifest.json
cat data/candidates/p111_wiki_linked_unified_v2/manifest.json
cat data/candidates/p111_wiki_linked_unified_all_mask_v2/manifest.json
cat data/candidates/p111_wiki_linked_length_v1/manifest.json
less -R data/candidates/p111_wiki_linked_native_v2/audit.jsonl
UV_LINK_MODE=copy uv run --offline python scripts/p111_wiki_linked_audit.py --native-dir data/candidates/p111_wiki_linked_native_v2 --target-manifest data/candidates/p111_wiki_linked_targets_v1/manifest.json --body-ledger data/candidates/p111_wiki_linked_body_support_v1/ledger.json --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p111_wiki_linked_to_unified.py --native-dir data/candidates/p111_wiki_linked_native_v2 --target-manifest data/candidates/p111_wiki_linked_targets_v1/manifest.json --body-ledger data/candidates/p111_wiki_linked_body_support_v1/ledger.json --output data/candidates/p111_wiki_linked_unified_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/audit_unified_reader_mask.py data/candidates/p111_wiki_linked_unified_v2 --all --output data/candidates/p111_wiki_linked_unified_all_mask_v2 --verify-only
UV_LINK_MODE=copy uv run --offline python scripts/p111_wiki_linked_length.py --native-dir data/candidates/p111_wiki_linked_native_v2 --raw-manifest data/candidates/p111_wiki_linked_raw_v1/manifest.json --target-manifest data/candidates/p111_wiki_linked_targets_v1/manifest.json --output-dir data/candidates/p111_wiki_linked_length_v1 --verify-only
UV_LINK_MODE=copy uv run --offline pytest -q tests/test_p111_wiki_linked.py
```
