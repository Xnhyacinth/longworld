from collections import Counter
from datetime import date

from longworld.core.asof import core_as_of, world_as_of
from longworld.core.engine import answer_from_artifacts
from longworld.core.process import (
    EXTENSION_TYPES,
    REGISTER_MARKERS,
    REGISTERS,
    attach_roles,
)
from longworld.core.sampler import materialize
from longworld.core.sourcepack import source_pack_artifacts
from longworld.core.topology import canonical_topology, effective_number
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view


def test_materialize_declares_strict_causal_closure() -> None:
    for domain, seed, query_type in (
        ("company", 1, "compare_belief"),
        ("codeforge", 4, "fork_join"),
        ("researchlab", 21, "contradiction"),
    ):
        mat = materialize(seed, n_parallel=0, n_pulses=0, domain=domain)
        world = mat.worlds["focal"]
        query = next(item for item in mat.queries if item.query_type == query_type)
        sufficient = set(query.sufficient_event_ids)
        artifacts = [
            artifact
            for artifact in mat.artifacts["focal"]
            if sufficient.intersection(artifact.reveals_events)
        ]

        assert (
            answer_from_artifacts(
                world,
                query,
                artifacts,
                enforce_preconditions=True,
            )
            == query.answer
        )


def test_materialize_rejects_unknown_domain() -> None:
    import pytest

    with pytest.raises(ValueError, match="unsupported domain"):
        materialize(1, domain="wikipedia")


def test_materialize_can_disable_program_join_rows() -> None:
    mat = materialize(4, domain="codeforge", include_program_joins=False)

    assert mat.queries
    assert not any(query.query_type == "program_join" for query in mat.queries)


def test_roles_are_not_just_names():
    mat = materialize(1, n_parallel=1, n_pulses=0, domain="company")
    org = mat.spec["focal"]["org"]
    assert org["counsel"]["role"] == "legal_counsel"
    assert org["counsel"]["name"]
    assert "file_amendment" in org["counsel"]["responsibilities"]
    arts = mat.artifacts["focal"]
    legal = next(a for a in arts if a.artifact_id.endswith("legal_email"))
    assert legal.slots.get("author_role") == "legal_counsel"
    assert "Author-role: legal_counsel" in legal.text or legal.role == "legal_counsel"


def test_core_as_of_excludes_rollback():
    found = False
    for seed in (1, 5, 7, 11):
        mat = materialize(seed, n_parallel=1, n_pulses=0, domain="company")
        world = mat.worlds["focal"]
        proc = world.spec["project"].get("process") or {}
        if not proc.get("rollback"):
            continue
        rb_ev = next(e for e in world.events if e.type == "rollback_amendment")
        assert core_as_of(world) < rb_ev.time
        assert core_as_of(world) < world_as_of(world)
        cur = next(
            q
            for q in mat.queries
            if q.query_type == "current_state" and "decoy" not in q.query_id
        )
        rb = next(q for q in mat.queries if q.query_type == "rollback_state")
        assert cur.answer != rb.answer
        assert cur.as_of is not None and rb.as_of is not None
        assert cur.as_of < rb.as_of
        assert cur.as_of < rb_ev.time
        found = True
        if proc.get("cascade"):
            break
    assert found


def test_process_queries_all_green_when_present():
    for domain, seed in (("company", 1), ("researchlab", 21), ("codeforge", 31)):
        mat = materialize(seed, n_parallel=1, n_pulses=0, domain=domain)
        world = mat.worlds["focal"]
        arts = mat.artifacts["focal"]
        kinds = {"exception_scope", "rollback_state", "exclusion"}
        found = [q for q in mat.queries if q.query_type in kinds]
        for spec in found:
            assert spec.essential_event_ids
            for eid in spec.essential_event_ids:
                leaf = eid.split(".")[-1]
                assert leaf in EXTENSION_TYPES or leaf in {
                    "sign_contract",
                    "report_v1",
                    "broken_commit",
                }
            _, cf_arts = render_cf_view(world, spec)
            ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
            assert ver.all_green(), (domain, spec.query_type, notes, ver)


def test_join_programs_are_diverse():
    canons: list[str] = []
    for domain, seed in (("company", 7), ("researchlab", 21), ("codeforge", 31)):
        mat = materialize(seed, n_parallel=1, n_pulses=0, domain=domain)
        joins = [q for q in mat.queries if q.query_type == "program_join"]
        assert joins, domain
        assert len(joins) <= 3, (domain, len(joins))
        canons.extend(canonical_topology(q) for q in joins)
    assert len(set(canons)) >= 3
    ne = effective_number(Counter(canons))
    assert ne >= 2.0


def test_compare_belief_and_cross_stream_gates():
    mat = materialize(1, n_parallel=1, n_pulses=0, domain="company")
    world = mat.worlds["focal"]
    arts = mat.artifacts["focal"]
    belief = next(q for q in mat.queries if q.query_type == "compare_belief")
    assert " || " in belief.answer
    assert len(belief.essential_artifact_ids) >= 3
    _, cf_arts = render_cf_view(world, belief)
    ver, notes = verify_question(world, spec := belief, arts, cf_artifacts=cf_arts)
    assert ver.all_green(), (notes, ver, spec.answer, spec.cf_answer)
    kinds = {q.query_type for q in mat.queries}
    for spec in mat.queries:
        if spec.query_type not in {"delayed_effect", "cross_stream"}:
            continue
        if "decoy" in spec.query_id:
            continue
        _, cf_arts = render_cf_view(world, spec)
        ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
        assert ver.all_green(), (spec.query_type, notes, ver, spec.answer)
    assert "compare_belief" in kinds


def test_announce_hold_does_not_print_jurisdiction():
    for seed in range(1, 12):
        mat = materialize(seed, n_parallel=1, n_pulses=0, domain="company")
        world = mat.worlds["focal"]
        if not (world.spec["project"].get("process") or {}).get("carveout"):
            continue
        j = str(world.spec["project"]["carveout_jurisdiction"])
        hold = next(
            a for a in mat.artifacts["focal"] if a.artifact_id.endswith("announce_hold")
        )
        assert "announcement-held" in hold.text
        assert j not in hold.text
        delayed = [q for q in mat.queries if q.query_type == "delayed_effect"]
        assert delayed and delayed[0].answer == j
        return
    raise AssertionError("no carveout world in seeds 1-11")


def test_join_canonical_is_order_invariant():
    from longworld.domains.company.queries import QuerySpec

    def spec_with(ops):
        return QuerySpec(
            query_id="t",
            query_type="program_join",
            question="",
            answer="a",
            as_of=None,
            answer_key="k",
            essential_event_ids=["e1", "e2"],
            essential_artifact_ids=["a1", "a2", "a3"],
            sufficient_event_ids=[],
            cf_event_id="e1",
            cf_param_updates={},
            cf_answer="b",
            invariance_event_id=None,
            program_ops=ops,
            domain="company",
            motif="x+y",
            proof_depth=3,
        )

    a = spec_with(
        [
            {"op": "LOOKUP", "query_type": "current_state"},
            {"op": "JOIN", "query_type": "multi_hop"},
        ]
    )
    b = spec_with(
        [
            {"op": "LOOKUP", "query_type": "multi_hop"},
            {"op": "JOIN", "query_type": "current_state"},
        ]
    )
    assert canonical_topology(a) == canonical_topology(b)


def test_source_pack_never_gold():
    arts = source_pack_artifacts("wtest", date(2026, 1, 8), n=6)
    if not arts:
        return
    assert all(a.role == "natural_background" for a in arts)
    assert all(not a.reveals_events for a in arts)
    assert all(len(a.text) > 400 for a in arts)


def test_source_pack_loads_longest_files_without_alpha_drop():
    from longworld.core.sourcepack import MAX_SOURCE_CHARS, PACK_DIR, _iter_texts

    if not PACK_DIR.is_dir():
        return
    on_disk = {
        p.stem
        for p in PACK_DIR.iterdir()
        if p.suffix.lower() in {".txt", ".md"} and p.name != "MANIFEST.json"
    }
    loaded = _iter_texts()
    stems = {stem for stem, _ in loaded}
    assert "rfc9110" in stems
    assert "rfc9112" in stems
    assert stems <= on_disk
    by_stem = dict(loaded)
    rfc = by_stem.get("rfc9110") or ""
    assert len(rfc) > 120000
    assert len(rfc) <= MAX_SOURCE_CHARS + 40
    arts = source_pack_artifacts("wtest", date(2026, 1, 8), n=24)
    leftover_stems = {str((a.slots or {}).get("source_stem") or "") for a in arts}
    assert "rfc9110" in leftover_stems
    # Longest-first: first leftover is not a short license.
    first = str((arts[0].slots or {}).get("source_stem") or "")
    assert first in {"rfc9110", "rfc5321", "llama2-2307.09288"}


def test_train_length_buckets_include_128k_256k():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from generate import _buckets_for_split

    cfg = {
        "length_buckets": {"16k": 16000, "128k": 128000, "256k": 256000},
        "eval_length_buckets": {},
    }
    train = _buckets_for_split(cfg, "train")
    assert train["128k"] == 128000
    assert train["256k"] == 256000


def test_source_grounded_pack_and_adopt_are_both_necessary():
    from longworld.core.engine import answer_from_artifacts

    found = False
    for domain, seed0 in (("company", 1), ("researchlab", 21), ("codeforge", 31)):
        for seed in range(seed0, seed0 + 12):
            mat = materialize(seed, n_parallel=1, n_pulses=0, domain=domain)
            specs = [q for q in mat.queries if q.query_type == "source_grounded"]
            if not specs:
                continue
            spec = specs[0]
            world = mat.worlds["focal"]
            arts = mat.artifacts["focal"]
            stem = spec.answer
            assert stem and stem != "unknown"
            assert stem not in spec.question
            assert spec.cf_answer != spec.answer
            assert spec.cf_answer != "unknown"
            adopt = next(a for a in arts if a.artifact_id.endswith("norm_adopt"))
            assert "public-norm-adopted" in adopt.text
            assert stem not in adopt.text
            pack = next(
                a for a in arts if a.artifact_id == spec.essential_artifact_ids[0]
            )
            assert stem in pack.text
            assert pack.reveals_events
            assert answer_from_artifacts(world, spec, [pack]) != spec.answer
            assert answer_from_artifacts(world, spec, [adopt]) != spec.answer
            _, cf_arts = render_cf_view(world, spec)
            ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
            assert ver.all_green(), (
                domain,
                seed,
                notes,
                ver,
                spec.answer,
                spec.cf_answer,
            )
            found = True
            break
        if found:
            break
    if not found:
        raise AssertionError("no grounded world in seed windows")


def test_source_choice_pair_distinct_from_grounded():
    from longworld.core.engine import answer_from_artifacts

    found = False
    for domain, seed0 in (("company", 1), ("researchlab", 21), ("codeforge", 31)):
        for seed in range(seed0, seed0 + 12):
            mat = materialize(seed, n_parallel=1, n_pulses=0, domain=domain)
            specs = [q for q in mat.queries if q.query_type == "source_choice"]
            grounded = [q for q in mat.queries if q.query_type == "source_grounded"]
            if not specs or not grounded:
                continue
            spec = specs[0]
            gspec = grounded[0]
            world = mat.worlds["focal"]
            arts = mat.artifacts["focal"]
            gold = spec.answer
            assert gold and gold != "unknown"
            assert " || " in gold
            adopted, unused = gold.split(" || ", 1)
            assert adopted == gspec.answer
            assert unused and unused != adopted
            assert adopted not in spec.question
            assert unused not in spec.question
            assert spec.cf_answer != spec.answer
            assert spec.cf_answer == f"{unused} || {adopted}"
            assert spec.proof_depth == 3
            assert len(spec.essential_artifact_ids) == 3
            assert len(spec.essential_event_ids) == 3
            by_id = {a.artifact_id: a for a in arts}
            primary_a = by_id[spec.essential_artifact_ids[0]]
            alt_a = by_id[spec.essential_artifact_ids[1]]
            adopt_a = by_id[spec.essential_artifact_ids[2]]
            assert adopted in primary_a.text
            assert unused in alt_a.text
            assert adopted not in adopt_a.text
            assert unused not in adopt_a.text
            assert answer_from_artifacts(world, spec, [primary_a, adopt_a]) != gold
            assert answer_from_artifacts(world, spec, [alt_a, adopt_a]) != gold
            assert answer_from_artifacts(world, spec, [primary_a, alt_a]) != gold
            assert (
                answer_from_artifacts(world, spec, [primary_a, alt_a, adopt_a]) == gold
            )
            joins = [q for q in mat.queries if q.query_type == "program_join"]
            assert all("source_choice" not in (q.motif or "") for q in joins)
            _, cf_arts = render_cf_view(world, spec)
            ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
            assert ver.all_green(), (
                domain,
                seed,
                notes,
                ver,
                gold,
                spec.cf_answer,
            )
            found = True
            break
        if found:
            break
    if not found:
        raise AssertionError("no source_choice world in seed windows")


def test_revisitation_three_hop_late_docs_omit_token():
    from longworld.core.engine import answer_from_artifacts

    found = False
    for domain, seed0 in (("company", 1), ("researchlab", 21), ("codeforge", 31)):
        for seed in range(seed0, seed0 + 16):
            mat = materialize(seed, n_parallel=1, n_pulses=0, domain=domain)
            specs = [q for q in mat.queries if q.query_type == "revisitation"]
            if not specs:
                continue
            spec = specs[0]
            world = mat.worlds["focal"]
            arts = mat.artifacts["focal"]
            token = spec.answer
            assert token and token.startswith("LT-")
            assert token not in spec.question
            assert spec.cf_answer != spec.answer
            assert spec.cf_answer != "unknown"
            assert spec.proof_depth == 3
            assert len(spec.essential_artifact_ids) == 3
            assert len(spec.essential_event_ids) == 3
            seed_a = next(a for a in arts if a.artifact_id.endswith("latent_seed"))
            ack_a = next(a for a in arts if a.artifact_id.endswith("latent_ack"))
            reopen_a = next(a for a in arts if a.artifact_id.endswith("latent_reopen"))
            assert token in seed_a.text
            assert token not in ack_a.text
            assert token not in reopen_a.text
            assert "latent-acked" in ack_a.text
            assert "case-reopened" in reopen_a.text
            decoy_a = next(
                (a for a in arts if a.artifact_id.endswith("latent_decoy")), None
            )
            if decoy_a is not None:
                decoy_tok = str(world.spec["project"].get("decoy_latent_token") or "")
                assert decoy_tok and decoy_tok.startswith("LD-")
                assert decoy_tok != token
                assert decoy_tok in decoy_a.text
                assert decoy_tok not in spec.question
                assert decoy_tok not in ack_a.text
                assert decoy_tok not in reopen_a.text
                assert decoy_a.artifact_id not in spec.essential_artifact_ids
                assert answer_from_artifacts(world, spec, [decoy_a]) != spec.answer
            assert answer_from_artifacts(world, spec, [seed_a]) != spec.answer
            assert answer_from_artifacts(world, spec, [ack_a]) != spec.answer
            assert answer_from_artifacts(world, spec, [reopen_a]) != spec.answer
            assert answer_from_artifacts(world, spec, [seed_a, ack_a]) != spec.answer
            assert answer_from_artifacts(world, spec, [ack_a, reopen_a]) != spec.answer
            assert (
                answer_from_artifacts(world, spec, [seed_a, ack_a, reopen_a])
                == spec.answer
            )
            joins = [q for q in mat.queries if q.query_type == "program_join"]
            assert all("revisitation" not in (q.motif or "") for q in joins)
            _, cf_arts = render_cf_view(world, spec)
            ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
            assert ver.all_green(), (domain, seed, notes, ver, token, spec.cf_answer)
            found = True
            break
        if found:
            break
    if not found:
        raise AssertionError("no cascade world in seed windows")


def test_attach_roles_names_last():
    org = attach_roles({"counsel": "Ada Example"}, {})
    assert org["counsel"]["role"] == "legal_counsel"
    assert org["counsel"]["name"] == "Ada Example"


_PROCEDURE_BLOBS = (
    "Why this file is not padding",
    "Identify the predecessor objects named in the causal graph",
    "It does not exist to occupy a token budget",
)


def test_discourse_is_register_not_universal_procedure():
    mat = materialize(1, n_parallel=1, n_pulses=0, domain="company")
    reg = mat.spec["focal"]["register"]
    assert reg in REGISTERS
    blob = "\n".join(a.text for a in mat.artifacts["focal"])
    assert all(s not in blob for s in _PROCEDURE_BLOBS)
    assert REGISTER_MARKERS[reg] in blob
    legal = next(
        a for a in mat.artifacts["focal"] if a.artifact_id.endswith("legal_email")
    )
    assert legal.slots.get("register") == reg
    plan = (legal.slots or {}).get("content_plan") or {}
    assert plan.get("style_cluster") == f"{reg}:{legal.doc_type}"
    assert plan["style_cluster"] != legal.doc_type


def test_registers_change_discourse_across_worlds():
    by_reg: dict[str, str] = {}
    for seed in range(1, 48):
        mat = materialize(seed, n_parallel=1, n_pulses=0, domain="company")
        reg = mat.spec["focal"]["register"]
        legal = next(
            a for a in mat.artifacts["focal"] if a.artifact_id.endswith("legal_email")
        )
        by_reg[reg] = legal.text
        if len(by_reg) >= 2:
            texts = list(by_reg.values())
            assert texts[0] != texts[1]
            markers = {REGISTER_MARKERS[r] for r in by_reg}
            assert all(any(m in t for m in markers) for t in texts)
            return
    raise AssertionError("need two workplace registers in seeds 1-47")


def test_ack_discourse_still_omits_gold_and_decoy_tokens():
    for domain, seed0 in (("company", 1), ("researchlab", 21), ("codeforge", 31)):
        for seed in range(seed0, seed0 + 16):
            mat = materialize(seed, n_parallel=1, n_pulses=0, domain=domain)
            specs = [q for q in mat.queries if q.query_type == "revisitation"]
            if not specs:
                continue
            arts = mat.artifacts["focal"]
            token = specs[0].answer
            decoy = str(
                mat.worlds["focal"].spec["project"].get("decoy_latent_token") or ""
            )
            ack = next(a for a in arts if a.artifact_id.endswith("latent_ack"))
            reopen = next(a for a in arts if a.artifact_id.endswith("latent_reopen"))
            assert token not in ack.text
            assert token not in reopen.text
            if decoy:
                assert decoy not in ack.text
                assert decoy not in reopen.text
            assert all(s not in ack.text for s in _PROCEDURE_BLOBS)
            return
    raise AssertionError("no cascade world in seed windows")


def test_ratification_four_hop_late_docs_omit_token():
    from longworld.core.engine import answer_from_artifacts

    found = False
    for domain, seed0 in (("company", 1), ("researchlab", 21), ("codeforge", 31)):
        for seed in range(seed0, seed0 + 16):
            mat = materialize(seed, n_parallel=1, n_pulses=0, domain=domain)
            specs = [q for q in mat.queries if q.query_type == "ratification"]
            if not specs:
                continue
            spec = specs[0]
            world = mat.worlds["focal"]
            arts = mat.artifacts["focal"]
            token = spec.answer
            assert token and token.startswith("LT-")
            assert token not in spec.question
            assert spec.cf_answer != spec.answer
            assert spec.cf_answer != "unknown"
            assert spec.proof_depth == 4
            assert len(spec.essential_artifact_ids) == 4
            assert len(spec.essential_event_ids) == 4
            seed_a = next(a for a in arts if a.artifact_id.endswith("latent_seed"))
            ack_a = next(a for a in arts if a.artifact_id.endswith("latent_ack"))
            reopen_a = next(a for a in arts if a.artifact_id.endswith("latent_reopen"))
            ratify_a = next(a for a in arts if a.artifact_id.endswith("latent_ratify"))
            assert token in seed_a.text
            assert token not in ack_a.text
            assert token not in reopen_a.text
            assert token not in ratify_a.text
            assert "case-ratified" in ratify_a.text
            decoy_a = next(
                (a for a in arts if a.artifact_id.endswith("latent_decoy")), None
            )
            if decoy_a is not None:
                decoy_tok = str(world.spec["project"].get("decoy_latent_token") or "")
                assert decoy_tok and decoy_tok.startswith("LD-")
                assert decoy_tok != token
                assert decoy_tok not in spec.question
                assert decoy_tok not in ratify_a.text
                assert decoy_a.artifact_id not in spec.essential_artifact_ids
            three = [seed_a, ack_a, reopen_a]
            assert answer_from_artifacts(world, spec, three) != spec.answer
            assert answer_from_artifacts(world, spec, three + [ratify_a]) == spec.answer
            rev = next(q for q in mat.queries if q.query_type == "revisitation")
            assert answer_from_artifacts(world, rev, three) == rev.answer
            joins = [q for q in mat.queries if q.query_type == "program_join"]
            assert all("ratification" not in (q.motif or "") for q in joins)
            _, cf_arts = render_cf_view(world, spec)
            ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
            assert ver.all_green(), (domain, seed, notes, ver, token, spec.cf_answer)
            found = True
            break
        if found:
            break
    if not found:
        raise AssertionError("no cascade world in seed windows")


def test_docket_control_distinct_gold_from_latent():
    from longworld.core.engine import answer_from_artifacts

    found = False
    for domain, seed0 in (("company", 1), ("researchlab", 21), ("codeforge", 31)):
        for seed in range(seed0, seed0 + 16):
            mat = materialize(seed, n_parallel=1, n_pulses=0, domain=domain)
            specs = [q for q in mat.queries if q.query_type == "docket_control"]
            if not specs:
                continue
            spec = specs[0]
            world = mat.worlds["focal"]
            arts = mat.artifacts["focal"]
            dock = spec.answer
            latent = str(world.spec["project"].get("latent_token") or "")
            assert dock and dock.startswith("DK-")
            assert latent and latent.startswith("LT-")
            assert dock != latent
            assert dock not in spec.question
            assert latent not in spec.question
            assert spec.cf_answer != spec.answer
            assert spec.proof_depth == 4
            assert len(spec.essential_artifact_ids) == 5
            seed_a = next(a for a in arts if a.artifact_id.endswith("latent_seed"))
            dock_a = next(a for a in arts if a.artifact_id.endswith("docket_seed"))
            ack_a = next(a for a in arts if a.artifact_id.endswith("latent_ack"))
            reopen_a = next(a for a in arts if a.artifact_id.endswith("latent_reopen"))
            ratify_a = next(a for a in arts if a.artifact_id.endswith("latent_ratify"))
            assert dock in dock_a.text
            assert dock not in seed_a.text
            assert dock not in ack_a.text
            assert dock not in reopen_a.text
            assert dock not in ratify_a.text
            assert latent in seed_a.text
            assert latent not in dock_a.text
            four_no_dock = [seed_a, ack_a, reopen_a, ratify_a]
            four_no_ratify = [seed_a, dock_a, ack_a, reopen_a]
            all_five = four_no_dock + [dock_a]
            assert answer_from_artifacts(world, spec, four_no_dock) != spec.answer
            assert answer_from_artifacts(world, spec, four_no_ratify) != spec.answer
            assert answer_from_artifacts(world, spec, all_five) == spec.answer
            rat = next(q for q in mat.queries if q.query_type == "ratification")
            assert rat.answer == latent
            assert rat.answer != dock
            joins = [q for q in mat.queries if q.query_type == "program_join"]
            assert all("docket_control" not in (q.motif or "") for q in joins)
            _, cf_arts = render_cf_view(world, spec)
            ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
            assert ver.all_green(), (domain, seed, notes, ver, dock, spec.cf_answer)
            found = True
            break
        if found:
            break
    if not found:
        raise AssertionError("no docket cascade world in seed windows")
