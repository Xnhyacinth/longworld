from longworld.core.contentplan import attach_content_plans, plan_from_artifact
from longworld.core.graph import hop_count
from longworld.core.proofprog import JOIN_SEP
from longworld.core.sampler import materialize
from longworld.core.topology import canonical_topology
from longworld.core.verify import verify_question
from longworld.core.views import render_cf_view


def test_company_program_join_is_necessary():
    mat = materialize(1, n_parallel=1, n_pulses=0, domain="company")
    joins = [q for q in mat.queries if q.query_type == "program_join"]
    assert len(joins) >= 1
    assert len(joins) <= 3
    spec = joins[0]
    assert JOIN_SEP in spec.answer
    assert spec.cf_answer != spec.answer
    assert len(spec.essential_artifact_ids) >= 3
    assert spec.program_ops[0]["op"] == "LOOKUP"
    assert spec.program_ops[1]["op"] == "JOIN"
    world = mat.worlds["focal"]
    arts = mat.artifacts["focal"]
    _, cf_arts = render_cf_view(world, spec)
    ver, notes = verify_question(world, spec, arts, cf_artifacts=cf_arts)
    assert ver.all_green(), notes
    assert hop_count(world, spec) >= 2
    assert "program_join" in canonical_topology(spec)


def test_lab_and_code_compose():
    for domain, seed in (("researchlab", 21), ("codeforge", 31)):
        mat = materialize(seed, n_parallel=1, n_pulses=0, domain=domain)
        joins = [q for q in mat.queries if q.query_type == "program_join"]
        assert joins, domain
        spec = joins[0]
        world = mat.worlds["focal"]
        _, cf_arts = render_cf_view(world, spec)
        ver, notes = verify_question(
            world, spec, mat.artifacts["focal"], cf_artifacts=cf_arts
        )
        assert ver.all_green(), (domain, notes, spec.answer, spec.cf_answer)


def test_content_plan_stays_out_of_text():
    mat = materialize(3, n_parallel=1, n_pulses=0)
    arts = attach_content_plans(mat.artifacts["focal"])
    a = arts[0]
    plan = plan_from_artifact(a)
    assert "content_plan" in a.slots
    assert "communicative_goal" in plan
    assert "content_plan" not in a.text
    assert '"new_propositions"' not in a.text
