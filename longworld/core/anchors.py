"""Frozen public-style anchors: search abstracts + code/tooling prose.

These are not the gold facts. They supply natural long-range distractor
language (SearchArt/ACC-like surface) while the executable world keeps
the answer. Each call mints unique artifact ids so packing never clones.
"""

from __future__ import annotations

from datetime import date, timedelta

from longworld.core.prose import unique_prose
from longworld.core.render import Artifact

# Public-knowledge distractors (generic methods / tooling). No leaderboard
# numerals and no company contract versions.
_SEARCH_DOCS = [
    (
        "search",
        "Dense retrieval ranks passages by embedding similarity. Contrastive "
        "in-batch negatives remain the default pretraining recipe. This note "
        "does not report a private benchmark score and must not be used as a "
        "substitute for an evaluation JSON inside a laboratory repository.",
    ),
    (
        "search",
        "A knowledge graph walk is a candidate generator, not a proof. Typed "
        "meta-paths beat uniform random walks because inverse edges and hubs "
        "otherwise fabricate fake multi-hop questions. Always verify that no "
        "shorter supporting page exists.",
    ),
    (
        "search",
        "Tool-using agents compile observations into long contexts. Correct "
        "final answers do not imply every observation was necessary. Effective "
        "horizon counts only steps that change the candidate set or state.",
    ),
    (
        "search",
        "Open web snapshots freeze a date. Alias resolution, URL canonicalization, "
        "and license checks belong in a registry before any world is simulated. "
        "Parametric memory of famous leaderboards is a leak, not a skill.",
    ),
]

_SEARCH_DOCS = [
    (
        "search",
        "Dense retrieval ranks passages by embedding similarity. Contrastive "
        "in-batch negatives remain the default pretraining recipe. This note "
        "does not report a private benchmark score and must not be used as a "
        "substitute for an evaluation JSON inside a laboratory repository.",
    ),
    (
        "search",
        "A knowledge graph walk is a candidate generator, not a proof. Typed "
        "meta-paths beat uniform random walks because inverse edges and hubs "
        "otherwise fabricate fake multi-hop questions. Always verify that no "
        "shorter supporting page exists.",
    ),
    (
        "search",
        "Tool-using agents compile observations into long contexts. Correct "
        "final answers do not imply every observation was necessary. Effective "
        "horizon counts only steps that change the candidate set or state.",
    ),
    (
        "search",
        "Open web snapshots freeze a date. Alias resolution, URL canonicalization, "
        "and license checks belong in a registry before any world is simulated. "
        "Parametric memory of famous leaderboards is a leak, not a skill.",
    ),
    (
        "search",
        "Multi-hop web questions often collapse to a single Wikipedia infobox. "
        "Dispersion of evidence across domains is a filter, not a guarantee that "
        "each hop is necessary. Remove-one tests still have to fail.",
    ),
    (
        "search",
        "Query-late memory training withholds the question until after the "
        "documents. Models that only answer when the query is prepended have "
        "not stored the early low-salience constraint.",
    ),
]

_CODE_DOCS = [
    (
        "code",
        "pytest cache is invalidated by --cache-clear. A tokenizer change that "
        "does not bump the eval fingerprint will silently reuse predictions. "
        "This paragraph is generic engineering folklore, not a lab log.",
    ),
    (
        "code",
        "Git notes: a six-hex commit is not a score. Release notes that refuse "
        "to reprint numerals force readers to open the artifact they adopt. "
        "Do not treat this file as an evaluation JSON.",
    ),
    (
        "code",
        "Issue templates should name the split card, the cache key, and the "
        "command that regenerates metrics. Filing an issue is an enabling "
        "event only inside the executable world that references it.",
    ),
    (
        "code",
        "SQL schemas for agent worlds keep state consistent across tools. "
        "A row update is an event. Replay from the log, never from a chatbot "
        "summary of the log.",
    ),
    (
        "code",
        "A git tag that refuses to reprint the HEAD hash forces the reader to "
        "open the hotfix object. CHANGELOG quotes of historical revisions are "
        "stale on purpose and are not shipping instruments.",
    ),
    (
        "code",
        "SPDX identifiers live in LICENSE. Release notes that adopt HEAD should "
        "not copy the identifier. Early license text is a hidden bridge, not a "
        "leaderboard.",
    ),
]


def public_anchor_artifacts(world_id: str, start: date, n: int = 8) -> list[Artifact]:
    pool = _SEARCH_DOCS + _CODE_DOCS
    out: list[Artifact] = []
    for i in range(n):
        kind, body = pool[i % len(pool)]
        day = start + timedelta(days=3 + i * 7)
        aid = f"{world_id}.anchor.{kind}.{i}"
        salt = f"{world_id[-12:]}:{kind}:{i}"
        text = (
            f"# Public {kind} anchor {i} (frozen)\n"
            f"Doc-id: {aid}\nDate: {day.isoformat()}\n"
            f"Surface-id: {salt}\n\n"
            f"{body}\n\n"
            f"This document is a real-schema distractor. It contains no private "
            f"state of {world_id}. Unique surface token {salt} prevents clone packing.\n\n"
            f"{unique_prose(salt, n=16)}"
        )
        out.append(
            Artifact(
                artifact_id=aid,
                doc_type=kind,
                time=day,
                project="public-anchor",
                prefix="anchor",
                reveals_events=[],
                text=text,
                facts=[],
                slots={"ground_values": [], "anchor": True},
                is_focal=False,
            )
        )
    return out
