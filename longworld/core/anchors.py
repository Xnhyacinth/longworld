"""Frozen public-style anchors: distinct documents, not a shuffled sentence bank.

Each body is unique. They are distractors / natural background, never gold.
"""

from __future__ import annotations

from datetime import date, timedelta

from longworld.core.render import Artifact

_DOCS: list[tuple[str, str]] = [
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
    (
        "search",
        "Citation graphs are not authority graphs. A paper that is widely cited "
        "for a method can still be wrong on a number. Do not treat an arXiv "
        "abstract as the private eval JSON of a lab world.",
    ),
    (
        "search",
        "Entity linking fails when two labs share a model nickname. Public "
        "leaderboards and internal reruns must be distinguished by dataset "
        "cards and split hashes, not by the string 'SOTA'.",
    ),
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
    (
        "code",
        "Continuous integration flakes are not product bugs. A red job that "
        "mentions a hash without a failing assertion is a scheduling artifact. "
        "Do not promote it to a release blocker from this paragraph alone.",
    ),
    (
        "code",
        "Dependency bots open pull requests that touch lockfiles and nothing "
        "else. Those diffs are not hotfixes. A tag that ships a lockfile bump "
        "does not adopt a failing test's expected hash.",
    ),
    (
        "rfc",
        "Internet-Draft style notes separate MUSTs from deployment gossip. A "
        "registry of media types is not a contract amendment. Readers looking "
        "for a delivery version will not find it here.",
    ),
    (
        "rfc",
        "Errata against a published RFC cite section numbers, not git tags. "
        "This file is a public-process distractor. It does not supersede an "
        "internal legal supplement or a private evaluation table.",
    ),
    (
        "minutes",
        "Public working-group minutes list attendance and deferred items. They "
        "are not finance restatements. Action items that say 'follow up next "
        "cycle' do not change recognized revenue or HEAD.",
    ),
    (
        "minutes",
        "Liaison statements between standards bodies quote charters. Quoted "
        "charters are not SPDX identifiers and are not contract versions. "
        "Treat this as natural background, not gold.",
    ),
    (
        "card",
        "A dataset card lists collection protocol, license, and known gaps. "
        "It does not contain the private rerun numeral for a fictional lab. "
        "Split names here are illustrative.",
    ),
    (
        "card",
        "Model cards report intended use and evaluation caveats. Public MMLU "
        "rehearsals are not the authoritative_score field of a simulated "
        "research world.",
    ),
    (
        "policy",
        "Export-control FAQs explain encryption classification at a high "
        "level. They are not LICENSE files of a named repository and do not "
        "set the SPDX of a tag.",
    ),
    (
        "policy",
        "Incident-response playbooks describe paging trees. A generic SEV-2 "
        "template is not a CI log and does not name a failing test token.",
    ),
]


def public_anchor_artifacts(world_id: str, start: date, n: int = 8) -> list[Artifact]:
    n = min(n, len(_DOCS))
    out: list[Artifact] = []
    for i in range(n):
        kind, body = _DOCS[i]
        day = start + timedelta(days=3 + i * 7)
        aid = f"{world_id}.anchor.{kind}.{i}"
        text = (
            f"# Public {kind} anchor {i} (frozen)\n"
            f"Doc-id: {aid}\nDate: {day.isoformat()}\n\n"
            f"{body}\n\n"
            f"This document is a real-schema distractor. It contains no private "
            f"state of {world_id}."
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
                role="natural_background",
            )
        )
    return out
