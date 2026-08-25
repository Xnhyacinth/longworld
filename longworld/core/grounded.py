"""GroundedWorld: real public docs enter the causal graph.

A source pack is not filler when it reveals ingest_public. Adopt copies the
pending stem from state (legal_supplement pattern). Gold is the stem; the
public file is necessary but does not by itself write public_norm.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from longworld.core.provenance import (
    ProvenanceError,
    load_verified_source_documents,
)
from longworld.core.realworkflow import parse_rfc_workflow
from longworld.core.render import Artifact
from longworld.core.sourcepack import PACK_DIR, source_pack_artifacts
from longworld.core.state import WorldState
from longworld.core.taxonomy import (
    EvidenceRole,
    SourceOrigin,
    WorkflowKind,
    classify_artifact,
)
from longworld.core.world import Event, SimulatedWorld

# Gold ingest artifacts must fit an 8k pack with the adopt memo.
INGEST_EXCERPT_CHARS = 8000

INGEST_TYPES = frozenset({"ingest_public", "ingest_public_alt"})
ADOPT_TYPE = "adopt_public"
SKIP_RENDER_TYPES = INGEST_TYPES

SOURCE_ROLES = (
    "normative_reference",
    "method_reference",
    "hard_negative",
    "benchmark_spec",
    "historical_version",
)


def list_stems() -> list[str]:
    if not PACK_DIR.is_dir():
        return []
    return sorted(
        p.stem
        for p in PACK_DIR.iterdir()
        if p.suffix.lower() in {".txt", ".md"} and p.name != "MANIFEST.json"
    )


def pick_anchors(rng, n_alt: int = 1) -> dict[str, str]:
    stems = list_stems()
    prefer = [
        s
        for s in (
            "rfc9110",
            "rfc8259",
            "rfc5321",
            "qwen-technical-report",
            "deepseek-v3",
            "attention-is-all-you-need",
            "bert-1810.04805",
            "llama2-2307.09288",
            "gpl-3.0",
            "apache-2.0",
        )
        if s in stems
    ]
    pool = prefer or stems
    if len(pool) < 2:
        return {}
    primary = rng.choice(pool)
    alt_pool = [s for s in pool if s != primary]
    alt = rng.choice(alt_pool)
    return {"public_norm_stem": primary, "public_alt_stem": alt}


def grounded_init() -> dict[str, Any]:
    return {
        "pending_public_primary": None,
        "pending_public_alt": None,
        "pending_public_primary_content": None,
        "pending_public_alt_content": None,
        "pending_public_primary_provenance_id": None,
        "pending_public_alt_provenance_id": None,
        "public_norm": None,
        "public_norm_content": None,
        "public_norm_provenance_id": None,
    }


def apply_grounded(state: WorldState, ev: Event) -> bool:
    t = ev.type
    p = ev.params
    eid, day = ev.id, ev.time
    if t == "ingest_public":
        state.set("pending_public_primary", p["stem"], eid, day)
        fact = p.get("content_fact")
        if isinstance(fact, dict) and fact.get("value"):
            state.set("pending_public_primary_content", str(fact["value"]), eid, day)
            state.set(
                "pending_public_primary_provenance_id",
                str(fact.get("provenance_id") or ""),
                eid,
                day,
            )
        return True
    if t == "ingest_public_alt":
        state.set("pending_public_alt", p["stem"], eid, day)
        fact = p.get("content_fact")
        if isinstance(fact, dict) and fact.get("value"):
            state.set("pending_public_alt_content", str(fact["value"]), eid, day)
            state.set(
                "pending_public_alt_provenance_id",
                str(fact.get("provenance_id") or ""),
                eid,
                day,
            )
        return True
    if t == ADOPT_TYPE:
        if p.get("adopt_alt"):
            src = state.values.get("pending_public_alt")
            content = state.values.get("pending_public_alt_content")
            provenance_id = state.values.get("pending_public_alt_provenance_id")
        else:
            src = state.values.get("pending_public_primary")
            content = state.values.get("pending_public_primary_content")
            provenance_id = state.values.get("pending_public_primary_provenance_id")
        if not src:
            return True
        state.set("public_norm", src, eid, day)
        if content:
            state.set("public_norm_content", content, eid, day)
            state.set("public_norm_provenance_id", provenance_id or "", eid, day)
        return True
    return False


def check_grounded(state: WorldState, ev: Event) -> tuple[bool, str | None] | None:
    if ev.type in INGEST_TYPES:
        return True, None
    if ev.type == ADOPT_TYPE:
        if ev.params.get("adopt_alt"):
            if not state.values.get("pending_public_alt"):
                return False, "no_alt_public"
        elif not state.values.get("pending_public_primary"):
            return False, "no_primary_public"
        return True, None
    return None


def grounded_events(
    project: dict[str, Any],
    prefix: str,
    start: date,
    *,
    ingest_off: int,
    alt_off: int,
    adopt_off: int,
    pack_dir: Path = PACK_DIR,
) -> list[Event]:
    if not (project.get("process") or {}).get("grounded"):
        return []
    primary = project.get("public_norm_stem")
    alt = project.get("public_alt_stem")
    if not primary or not alt or primary == alt:
        return []

    def eid(kind: str) -> str:
        return f"{prefix}.{kind}"

    content_facts = _verified_content_facts(pack_dir)
    primary_fact = content_facts.get(str(primary))
    alt_fact = content_facts.get(str(alt))

    return [
        Event(
            id=eid("ingest_public"),
            type="ingest_public",
            time=start + timedelta(days=ingest_off),
            params={
                "stem": primary,
                "slot": "primary",
                **({"content_fact": primary_fact} if primary_fact else {}),
            },
            visibility=[f"{prefix}.source.{primary}"],
            causal_inputs=[],
            relation_kinds={},
        ),
        Event(
            id=eid("ingest_public_alt"),
            type="ingest_public_alt",
            time=start + timedelta(days=alt_off),
            params={
                "stem": alt,
                "slot": "alt",
                **({"content_fact": alt_fact} if alt_fact else {}),
            },
            visibility=[f"{prefix}.source.{alt}"],
            causal_inputs=[],
        ),
        Event(
            id=eid("adopt_public"),
            type="adopt_public",
            time=start + timedelta(days=adopt_off),
            params={"adopt_pending": True, "slot": "primary"},
            visibility=[f"{prefix}.norm_adopt"],
            causal_inputs=[eid("ingest_public")],
            relation_kinds={eid("ingest_public"): "derived_from"},
        ),
    ]


def _verified_content_facts(pack_dir: Path = PACK_DIR) -> dict[str, dict[str, str]]:
    """Extract one stable identity fact from each strictly verified RFC source."""
    try:
        documents = load_verified_source_documents(pack_dir)
    except ProvenanceError:
        return {}
    facts: dict[str, dict[str, str]] = {}
    for document in documents:
        assert document.lineage is not None
        try:
            workflow = parse_rfc_workflow(document.text, document.lineage)
        except ProvenanceError:
            continue
        fact = workflow.facts["document_id"]
        facts[document.stem] = {
            "key": fact.key,
            "value": fact.value,
            "provenance_id": document.lineage.provenance_id,
            "record_id": fact.record_id,
            "char_start": str(fact.char_start),
            "char_end": str(fact.char_end),
        }
    return facts


def bind_source_packs(
    world: SimulatedWorld, artifacts: list[Artifact]
) -> list[Artifact]:
    """Replace ingest stubs with real public texts; leftover packs are background.

    Ingest-bound packs are short excerpts so 8k/16k slots still pack. Full
    leftover files attach only on the focal world so extra-filler worlds do
    not multiply 16 RFCs.
    """
    start = date.fromisoformat(str(world.spec.get("start") or "2026-01-08"))
    packs = source_pack_artifacts(
        world.spec["world_id"], start, n=24, deny_substrings=None
    )
    by_stem = {}
    for a in packs:
        stem = str((a.slots or {}).get("source_stem") or "")
        if stem:
            by_stem[stem] = a
    ingest_by_stem: dict[str, Event] = {}
    for ev in world.events:
        if ev.type in INGEST_TYPES and not ev.skipped:
            ingest_by_stem[str(ev.params.get("stem") or "")] = ev
    out = [a for a in artifacts if a.doc_type != "source_pack"]
    used: set[str] = set()
    prefix = str(world.spec.get("prefix") or "focal")
    proj_focal = bool(
        (world.spec.get("project") or {}).get("is_focal", prefix == "focal")
    )
    for stem, ev in ingest_by_stem.items():
        pack = by_stem.get(stem)
        if pack is None:
            continue
        vis = ev.visibility[0]
        text = pack.text
        if stem not in text:
            text = f"Source-stem: {stem}\n" + text
        full_text = text
        excerpt = text
        if len(excerpt) > INGEST_EXCERPT_CHARS:
            excerpt = excerpt[:INGEST_EXCERPT_CHARS] + "\n[excerpt of public source]"
        pack.artifact_id = f"{world.spec['world_id']}.{vis}"
        pack.reveals_events = [ev.id]
        pack.time = ev.time
        pack.prefix = prefix
        pack.is_focal = proj_focal and ev.type == "ingest_public"
        pack.role = (
            "normative_reference" if ev.type == "ingest_public" else "hard_negative"
        )
        pack.text = excerpt
        content_fact = ev.params.get("content_fact")
        content_value = (
            str(content_fact.get("value") or "")
            if isinstance(content_fact, dict)
            else ""
        )
        pack.slots = {
            **(pack.slots or {}),
            "event_type": ev.type,
            "ground_values": [value for value in (stem, content_value) if value],
            "source_role": pack.role,
            "source_stem": stem,
            "workstream": "public",
            "content_plan": {
                "artifact_type": "source_pack",
                "communicative_goal": "ingest_public",
                "new_propositions": [f"source:{stem}"],
            },
        }
        verified = bool(pack.slots.get("provenance_verified"))
        lineage = dict(pack.slots.get("source_lineage") or {})
        classify_artifact(
            pack,
            source_origin=(
                SourceOrigin.REAL_PUBLIC if verified else SourceOrigin.UNKNOWN
            ),
            workflow_kind=(
                WorkflowKind.REAL_SOURCE_DERIVED
                if verified
                else WorkflowKind.BACKGROUND_ONLY
            ),
            evidence_role=(
                (
                    EvidenceRole.CAUSAL_GOLD
                    if ev.type == "ingest_public"
                    else EvidenceRole.STRUCTURAL_HARD_NEGATIVE
                )
                if verified
                else EvidenceRole.NATURAL_BACKGROUND
            ),
            workflow_id=(
                str(world.spec["world_id"]) if verified else f"legacy-source:{stem}"
            ),
            provenance_id=str(lineage.get("provenance_id") or "") if verified else "",
        )
        out.append(pack)
        used.add(stem)
        if (
            prefix == "focal"
            and proj_focal
            and len(full_text) > INGEST_EXCERPT_CHARS + 400
        ):
            full_artifact = Artifact(
                artifact_id=f"{world.spec['world_id']}.source.{stem}.full",
                doc_type="source_pack",
                time=ev.time,
                project="public",
                prefix="source",
                reveals_events=[],
                text=full_text,
                facts=[],
                slots={
                    "event_type": "source_pack",
                    "ground_values": [],
                    "source_stem": stem,
                    "source_role": "method_reference",
                    "workstream": "public",
                    "provenance_verified": verified,
                    "source_lineage": lineage,
                    "training_objective": ("unassigned" if verified else "diagnostic"),
                    "content_plan": {
                        "artifact_type": "source_pack",
                        "communicative_goal": "public_fulltext",
                        "new_propositions": [f"source-full:{stem}"],
                    },
                },
                is_focal=False,
                role="method_reference",
            )
            classify_artifact(
                full_artifact,
                source_origin=(
                    SourceOrigin.REAL_PUBLIC if verified else SourceOrigin.UNKNOWN
                ),
                workflow_kind=(
                    WorkflowKind.REAL_SOURCE_DERIVED
                    if verified
                    else WorkflowKind.BACKGROUND_ONLY
                ),
                evidence_role=(
                    EvidenceRole.CAUSAL_SUPPORTING
                    if verified
                    else EvidenceRole.NATURAL_BACKGROUND
                ),
                workflow_id=(
                    str(world.spec["world_id"]) if verified else f"legacy-source:{stem}"
                ),
                provenance_id=(
                    str(lineage.get("provenance_id") or "") if verified else ""
                ),
            )
            out.append(full_artifact)
    if prefix == "focal" and proj_focal:
        for stem, pack in by_stem.items():
            if stem in used:
                continue
            pack.role = "natural_background"
            pack.is_focal = False
            plan = dict((pack.slots or {}).get("content_plan") or {})
            if not plan.get("new_propositions"):
                plan["new_propositions"] = [f"source:{stem}"]
            pack.slots = {
                **(pack.slots or {}),
                "source_role": "natural_background",
                "workstream": "public",
                "ground_values": list(
                    (pack.slots or {}).get("ground_values") or [stem]
                ),
                "content_plan": plan,
            }
            out.append(pack)
    out.sort(key=lambda a: (a.time, a.artifact_id))
    return out


def build_source_grounded_query(
    world: SimulatedWorld,
    invariance_event_id: str,
    invariance_param_updates: dict[str, Any],
):
    """Public file + private adopt memo. Stem is gold; memo must not print it."""
    from longworld.core.asof import find_event, world_as_of
    from longworld.domains.company.queries import QuerySpec, instance_topology

    ingest = find_event(world, "ingest_public")
    adopt = find_event(world, "adopt_public")
    if ingest is None or adopt is None:
        return None
    project = world.spec["project"]
    primary = str(ingest.params.get("stem") or project.get("public_norm_stem") or "")
    alt = str(project.get("public_alt_stem") or "")
    if not primary or not alt or primary == alt:
        return None
    domain = str(world.spec.get("domain") or "company")
    if domain == "researchlab":
        label = str(project.get("paper") or "the paper")
    elif domain == "codeforge":
        label = str(project.get("repo") or "the repository")
    else:
        label = str(project.get("project") or "the project")
    qid = world.spec["world_id"].split(":")[0]
    prefix = world.spec["prefix"]
    spec = QuerySpec(
        query_id=f"{qid}:source_grounded",
        query_type="source_grounded",
        question=(
            f"Which public-document stem did the standards desk adopt as the "
            f"external normative reference for {label}? Reconstruct it from the "
            f"ingested public file together with the internal adoption memo. "
            f"The memo does not reprint the stem. Alternate public files on the "
            f"desk are not adopted. Reply with the source-pack stem only."
        ),
        answer="",
        as_of=world_as_of(world),
        answer_key="public_norm",
        essential_event_ids=[ingest.id, adopt.id],
        essential_artifact_ids=[
            f"{world.spec['world_id']}.{prefix}.source.{primary}",
            f"{world.spec['world_id']}.{prefix}.norm_adopt",
        ],
        sufficient_event_ids=[ingest.id, adopt.id],
        cf_event_id=adopt.id,
        cf_param_updates={"adopt_alt": True},
        cf_answer="",
        invariance_event_id=invariance_event_id,
        invariance_param_updates=dict(invariance_param_updates),
        gold_expression="public_norm copies pending_public_primary at adopt",
        proof_depth=2,
        cf_op="version",
        motif="source_grounded",
        topology_id=instance_topology(f"{domain}.public_norm_adopt", label, primary),
        domain=domain,
        truth_regime="grounded_public_private",
    )
    if primary in spec.question or alt in spec.question:
        return None
    return spec


def build_content_grounded_query(
    world: SimulatedWorld,
    invariance_event_id: str,
    invariance_param_updates: dict[str, Any],
):
    """Build a query whose state and answer come from verified source text."""
    from longworld.core.asof import find_event, world_as_of
    from longworld.domains.company.queries import QuerySpec, instance_topology

    ingest = find_event(world, "ingest_public")
    alt_ingest = find_event(world, "ingest_public_alt")
    adopt = find_event(world, ADOPT_TYPE)
    if ingest is None or alt_ingest is None or adopt is None:
        return None
    primary_fact = ingest.params.get("content_fact")
    alt_fact = alt_ingest.params.get("content_fact")
    if not isinstance(primary_fact, dict) or not isinstance(alt_fact, dict):
        return None
    primary_value = str(primary_fact.get("value") or "")
    alt_value = str(alt_fact.get("value") or "")
    fact_key = str(primary_fact.get("key") or "")
    if (
        not primary_value
        or not alt_value
        or primary_value == alt_value
        or fact_key != str(alt_fact.get("key") or "")
        or not primary_fact.get("provenance_id")
        or not alt_fact.get("provenance_id")
    ):
        return None
    project = world.spec["project"]
    domain = str(world.spec.get("domain") or "company")
    if domain == "researchlab":
        label = str(project.get("paper") or "the paper")
    elif domain == "codeforge":
        label = str(project.get("repo") or "the repository")
    else:
        label = str(project.get("project") or "the project")
    qid = world.spec["world_id"].split(":")[0]
    prefix = world.spec["prefix"]
    spec = QuerySpec(
        query_id=f"{qid}:content_grounded",
        query_type="content_grounded",
        question=(
            f"The standards desk approved its primary public intake for {label}. "
            f"What Request for Comments identifier appears in that adopted "
            f"document's header? Reply exactly as RFC <number>."
        ),
        answer="",
        as_of=world_as_of(world),
        answer_key="public_norm_content",
        essential_event_ids=[ingest.id, adopt.id],
        essential_artifact_ids=[
            f"{world.spec['world_id']}.{prefix}.source.{ingest.params['stem']}",
            f"{world.spec['world_id']}.{prefix}.norm_adopt",
        ],
        sufficient_event_ids=[ingest.id, adopt.id],
        cf_event_id=adopt.id,
        cf_param_updates={"adopt_alt": True},
        cf_answer="",
        invariance_event_id=invariance_event_id,
        invariance_param_updates=dict(invariance_param_updates),
        gold_expression=(
            "parse RFC document_id from verified source text; adopt selects intake"
        ),
        proof_depth=2,
        cf_op="content_lookup",
        motif="content_grounded",
        topology_id=instance_topology(
            f"{domain}.verified_content_adopt", label, fact_key
        ),
        domain=domain,
        truth_regime="verified_real_content_hybrid",
        program_ops=[
            {
                "op": "CONTENT_LOOKUP",
                "field": fact_key,
                "provenance_required": True,
            },
            {"op": "ADOPT_PRIMARY", "event_id": adopt.id},
        ],
    )
    if primary_value in spec.question or alt_value in spec.question:
        return None
    return spec


def eval_source_choice(values: dict[str, Any]) -> str:
    """Adopted stem vs the ingested file that was not adopted."""
    adopted = values.get("public_norm")
    primary = values.get("pending_public_primary")
    alt = values.get("pending_public_alt")
    if not adopted or not primary or not alt or primary == alt:
        return "unknown"
    if adopted == primary:
        unused = alt
    elif adopted == alt:
        unused = primary
    else:
        return "unknown"
    return f"{adopted} || {unused}"


def build_source_choice_query(
    world: SimulatedWorld,
    invariance_event_id: str,
    invariance_param_updates: dict[str, Any],
):
    """Adopted public stem vs unused ingest. Pair gold; adopt does not reprint."""
    from longworld.core.asof import find_event, world_as_of
    from longworld.domains.company.queries import QuerySpec, instance_topology

    ingest = find_event(world, "ingest_public")
    alt_ev = find_event(world, "ingest_public_alt")
    adopt = find_event(world, ADOPT_TYPE)
    if ingest is None or alt_ev is None or adopt is None:
        return None
    project = world.spec["project"]
    primary = str(ingest.params.get("stem") or project.get("public_norm_stem") or "")
    alt = str(alt_ev.params.get("stem") or project.get("public_alt_stem") or "")
    if not primary or not alt or primary == alt:
        return None
    domain = str(world.spec.get("domain") or "company")
    if domain == "researchlab":
        label = str(project.get("paper") or "the paper")
    elif domain == "codeforge":
        label = str(project.get("repo") or "the repository")
    else:
        label = str(project.get("project") or "the project")
    qid = world.spec["world_id"].split(":")[0]
    prefix = world.spec["prefix"]
    spec = QuerySpec(
        query_id=f"{qid}:source_choice",
        query_type="source_choice",
        question=(
            f"For {label}, which public-document stem did the standards desk "
            f"adopt as the external normative reference, and which ingested "
            f"public file was left unused? The adoption memo does not reprint "
            f"either stem. Reply exactly as: <adopted_stem> || <unused_stem>."
        ),
        answer="",
        as_of=world_as_of(world),
        answer_key="public_norm",
        essential_event_ids=[ingest.id, alt_ev.id, adopt.id],
        essential_artifact_ids=[
            f"{world.spec['world_id']}.{prefix}.source.{primary}",
            f"{world.spec['world_id']}.{prefix}.source.{alt}",
            f"{world.spec['world_id']}.{prefix}.norm_adopt",
        ],
        sufficient_event_ids=[ingest.id, alt_ev.id, adopt.id],
        cf_event_id=adopt.id,
        cf_param_updates={"adopt_alt": True},
        cf_answer="",
        invariance_event_id=invariance_event_id,
        invariance_param_updates=dict(invariance_param_updates),
        gold_expression=(
            "public_norm || unused pending stem; adopt chooses which ingest is unused"
        ),
        proof_depth=3,
        cf_op="version",
        motif="source_choice",
        topology_id=instance_topology(
            f"{domain}.public_norm_choice", label, primary, alt
        ),
        domain=domain,
        truth_regime="grounded_public_private",
    )
    if primary in spec.question or alt in spec.question:
        return None
    return spec
