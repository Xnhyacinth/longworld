"""Finite-rule induction and workflow state replay, with separate oracles.

The workflow family is offline replay under a fixed policy: execute_job() picks
every action from the module-level constant table below, so the supervised
targets are replayed state and next-action prediction, not model-selected
closed-loop control. The lineage states that explicitly (execution_mode,
closed_loop) and the capability tag is L3, not L5.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import random
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# The visible task instruction and the world protocol are part of the task
# contract: solve_visible() consumes the structured fields, so a tampered or
# paraphrased prompt that contradicts the protocol would still validate.
# validate_bundle() therefore pins both to these constants and rejects any
# deviation. Changing the wording means changing the constant, and every
# historical bundle with the old wording then fails validation loudly.
PROMPTS: dict[str, str] = {
    "rule_learning": "Infer the unique affine rule from demonstrations, sum every update per entity, and return entity/label pairs sorted by entity.",
    "workflow": "Replay dependent jobs in record order. Given observed first-attempt statuses, return last_job, its final_value, the number of jobs requiring recovery in this partition (recovery_count), and next_action_for_last_job after its observed first attempt. Earlier partitions still determine dependency values.",
    # One wording per partition: the split rows are one question per row, so a
    # single shared sentence would be one instruction serving every row -- the
    # instruction concentration the collapse gate measures. The wording also
    # names the expected answer schema, because the closing line of the user
    # turn is one constant for the auditor. {partition} is the only
    # substitution and it is pin-checked by validate_bundle().
    "workflow_split": "Replay the visible jobs in record order and answer for world state after the question's cutoff record with its frozen-prefix state (last_job, final_value, recovery_count, next_action_for_last_job after its observed first attempt); recovery_count counts the jobs requiring recovery in part {partition}. Records after the cutoff are not part of the question.",
}

# Capability tags. The workflow policy is a fixed dictionary, so this is
# offline state replay with next-action prediction (L3), never closed-loop
# execution (L5).
RULE_LEARNING_CAPABILITY = "L4_rule_induction"
WORKFLOW_CAPABILITY = "L3_state_replay_offline_action_prediction"
PACKAGING_SCHEMA = "longworld.rules-workflow-packaging.v1"
DEFAULT_PACKAGING = {"rule_learning": "joint", "workflow": "split"}
SPLIT_HEAD_PERCENT = 93

# Closing answer instruction. It is one constant for every family, packaging
# and row because scripts/audit_capability_curriculum.py parses the user turn
# by stripping exactly this suffix from the parsed question payload; any other
# wording, and any extra line anywhere in the turn, is invisible to that
# auditor (it checks the context and topic byte-for-byte). Per-question
# wording therefore lives in the questions payload, where the auditor reads it.
ANSWER_SUFFIX = "\nReturn one JSON object mapping every question id to its answer."


def prompt_for(family: str, packaging: str = "joint", partition: int = 0) -> str:
    """Pinned visible instruction for this family under this packaging mode."""
    if family == "workflow" and packaging == "split":
        return PROMPTS["workflow_split"].format(partition=partition)
    return PROMPTS[family]


def capability_for(family: str) -> str:
    return RULE_LEARNING_CAPABILITY if family == "rule_learning" else WORKFLOW_CAPABILITY


def dependency_metadata(family: str, packaging: str = "joint") -> dict[str, Any]:
    """Per-task label for what an answer would need if a reader took a shortcut.

    `dependency_given` is honest, not hidden: in the joint workflow row the
    answers form one reversible chain, so an earlier answer plus the later
    partition slice determines the later answer and the long read is not
    required. Split rows carry exactly one answer over their own visible
    record prefix, so the full prefix is the only route to it.
    """
    if family != "workflow":
        return {
            "answer_dependency_mode": "independent",
            "basis": "one question per entity/alias partition; no answer shares evidence with another",
        }
    if packaging == "split":
        return {
            "answer_dependency_mode": "independent",
            "basis": "value chain replayed over this row's visible record prefix; no sibling answer is present in the row",
            "visible_prefix_self_contained": True,
        }
    return {
        "answer_dependency_mode": "dependency_given",
        "basis": "value = ((prior + payload) * multiplier) % 100003 and depends_on is the preceding record, so an earlier partition's answer plus this partition's records determines this answer",
        "visible_prefix_self_contained": False,
    }

PROTOCOLS: dict[str, str] = {
    "rule_learning": "Unknown a,b,c define labels[(a*x+b*y+c) mod 7]. Infer the rule from demonstrations. Each entity starts x=y=0; apply all dx/dy increments. Labels are the ordered output alphabet. dx and dy are integers 1..6.",
    "workflow": "Jobs form a dependency chain in record order. value=((dependency committed value or 0)+payload)*multiplier mod 100003. Each record reports the observed result of an initial inspect/process attempt; checksum_error reveals failure, processed reveals success. A failed job requires repair (failed->repaired, checksum_restored), process (repaired->processed, decimal value), commit (processed->committed, durable). A processed job only requires commit. No future recovery result is included. Return job,continuation,value; transitions have action,before,after,feedback. Replay earlier jobs to resolve dependencies.",
}


def _infer(doc: dict) -> tuple[int, int, int]:
    p, labels = doc["modulus"], doc["labels"]
    hypotheses = [(a, b, c) for a, b, c in itertools.product(range(p), repeat=3)
                  if all(labels[(a*d["x"]+b*d["y"]+c) % p] == d["label"]
                         for d in doc["demonstrations"])]
    if len(hypotheses) != 1:
        raise ValueError("demonstrations must identify exactly one rule")
    return hypotheses[0]


def step_job(job: dict, state: str, action: str) -> dict:
    """Actual tool boundary. Hidden fault is consulted only on process invocation."""
    before = state
    if (state, action) == ("new", "inspect"):
        state, result = "ready", "ready"
    elif (state, action) == ("ready", "process"):
        state, result = ("failed", "checksum_error") if job["fault"] else ("processed", str(job["value"]))
    elif (state, action) == ("failed", "repair"):
        state, result = "repaired", "checksum_restored"
    elif (state, action) == ("repaired", "process"):
        state, result = "processed", str(job["value"])
    elif (state, action) == ("processed", "commit"):
        state, result = "committed", "durable"
    else:
        raise ValueError(f"illegal transition: {state} {action}")
    return {"action": action, "before": before, "after": state, "feedback": result}


# The replay policy is a constant table, not a model decision: every action is
# a function of the state alone, so the workflow family supervises offline
# action prediction against a fixed policy rather than closed-loop control.
_POLICY: dict[str, str] = {
    "new": "inspect",
    "ready": "process",
    "failed": "repair",
    "repaired": "process",
    "processed": "commit",
}


def execute_job(job: dict) -> dict:
    job = {**job, "value": job.get("value", job["payload"] * job["multiplier"])}
    state, trace = "new", []
    while state != "committed":
        receipt = step_job(job, state, _POLICY[state])
        trace.append(receipt)
        state = receipt["after"]
    return {"job": job["id"], "trace": trace, "value": job["value"]}


def _workflow_oracle(records: list[dict]) -> list[dict]:
    outputs, receipts = {}, []
    for row in records:
        prior = outputs[row["depends_on"]] if row["depends_on"] else 0
        value = (prior + row["payload"]) * row["multiplier"] % 100003
        receipt = execute_job({**row, "fault": row["observed_status"] == "checksum_error", "value": value})
        outputs[row["id"]] = receipt["value"]
        # Only the continuation after observed initial attempt is a supervised target.
        receipts.append({"job": row["id"], "continuation": receipt["trace"][2:], "value": receipt["value"]})
    return receipts


def _rule_oracle(doc: dict, coefficients: tuple[int, int, int]) -> list[dict]:
    a, b, c = coefficients
    states = {}
    for row in doc["records"]:
        # Increment label-space state directly, independently of solver x/y aggregation.
        states.setdefault(row["entity"], c)
        states[row["entity"]] = (states[row["entity"]] + a*row["dx"] + b*row["dy"]) % doc["modulus"]
    return [{"entity": e, "label": doc["labels"][v]} for e, v in sorted(states.items())]


def solve_visible(context: str, question: dict | str) -> list[dict] | dict:
    doc = json.loads(context)
    query = json.loads(question) if isinstance(question, str) else question
    group = query["partition"]
    if not 0 <= group < doc["partitions"]:
        raise ValueError("invalid partition")
    cutoff = query.get("cutoff")
    if cutoff is not None and not isinstance(cutoff, str):
        raise ValueError("invalid cutoff")
    if doc["family"] == "rule_learning":
        a, b, c = _infer(doc)
        totals = {}
        for row in doc["records"]:
            if row["partition"] == group:
                x, y = totals.get(row["entity"], (0, 0))
                totals[row["entity"]] = x + row["dx"], y + row["dy"]
        return [{"entity": e, "label": doc["labels"][(a*x+b*y+c) % doc["modulus"]]}
                for e, (x, y) in sorted(totals.items())]
    if doc["family"] != "workflow":
        raise ValueError("unknown family")
    # Independent visible-input evaluator: no tool executor or hidden-state oracle.
    outputs, answers = {}, []
    for row in doc["records"]:
        prior = outputs[row["depends_on"]] if row["depends_on"] else 0
        value = ((prior + row["payload"]) * row["multiplier"]) % 100003
        outputs[row["id"]] = value
        if cutoff is not None and row["id"] > cutoff:
            # Everything after the cutoff is outside the question: the frozen
            # state is the last record at or before it, in this partition.
            continue
        trace = []
        if row["observed_status"] == "checksum_error":
            trace = [{"action": "repair", "before": "failed", "after": "repaired", "feedback": "checksum_restored"},
                     {"action": "process", "before": "repaired", "after": "processed", "feedback": str(value)}]
        elif row["observed_status"] != "processed":
            raise ValueError("missing or invalid observed tool feedback")
        trace.append({"action": "commit", "before": "processed", "after": "committed", "feedback": "durable"})
        if row["partition"] == group:
            answers.append({"job": row["id"], "continuation": trace, "value": value})
    return _compact_workflow(answers)


def _compact_workflow(receipts: list[dict]) -> dict:
    last = receipts[-1]
    return {"last_job": last["job"], "final_value": last["value"],
            "recovery_count": sum(len(row["continuation"]) > 1 for row in receipts),
            "next_action_for_last_job": last["continuation"][0]["action"]}


def _split_partitions(n_records: int, n_questions: int) -> list[int]:
    """Partition schedule for split packaging: one long head, a short tail.

    Split rows are one question per row and every row has to stay input-long,
    so the first question cannot own 1/16 of the world -- its prefix would be
    1/16 of a capacity-sized context. The first partition holds
    SPLIT_HEAD_PERCENT of the records and the remaining partitions share the
    tail, which keeps the shortest prefix within 0.93 of the longest (the 90%-
    of-capacity invariant needs at least 0.923). The cap keeps at least one
    record in every partition, so a small world degenerates to a shorter head
    rather than an empty partition.
    """
    head = min(n_records * SPLIT_HEAD_PERCENT // 100, n_records - (n_questions - 1))
    tail = n_records - head
    schedule = [0] * head
    for index in range(head, n_records):
        schedule.append(1 + min(n_questions - 2, (index - head) * (n_questions - 1) // tail))
    return schedule


def _cutoffs(doc: dict) -> dict[int, str]:
    """Last record id of each partition: the frozen-state cutoff of that group."""
    last = {}
    for row in doc["records"]:
        last[row["partition"]] = row["id"]
    return last


def workflow_questions(doc: dict) -> list[dict]:
    """One generic question per partition, keyed to that partition's cutoff.

    The question carries only the partition and the cutoff record id, so a row
    built from it supervises the frozen state after that record and nothing
    about the records that follow.
    """
    if doc["family"] != "workflow":
        raise ValueError("workflow questions require a workflow document")
    cutoffs = _cutoffs(doc)
    return [
        {"id": f"q{partition:03d}", "question": {"partition": partition, "cutoff": cutoffs[partition]},
         "cutoff": cutoffs[partition]}
        for partition in range(doc["partitions"])
    ]


def workflow_context(doc: dict, cutoff: str | None = None) -> str:
    """Visible context ending at the cutoff record; the row's own prefix.

    A split row carries only the records that its question can see, so the
    answer is a function of exactly the visible input.
    """
    if doc["family"] != "workflow":
        raise ValueError("workflow contexts require a workflow document")
    if cutoff is None:
        return _json(doc)
    if cutoff not in {row["id"] for row in doc["records"]}:
        raise ValueError("cutoff is not a visible record")
    return _json({**doc, "records": [row for row in doc["records"] if row["id"] <= cutoff]})


def workflow_answer(doc: dict, partition: int, cutoff: str | None = None) -> dict:
    """Gold for one workflow question, evaluated at the frozen prefix.

    Independent of the visible solver: the state comes from the tool executor
    receipts, so validate_bundle() cross-checks it against solve_visible()
    rather than restating it. `cutoff=None` covers the whole partition, which
    is the joint question; a cutoff restricts the replay to records up to and
    including that id.
    """
    if doc["family"] != "workflow":
        raise ValueError("workflow answers require a workflow document")
    if cutoff is None:
        selected = {row["id"] for row in doc["records"] if row["partition"] == partition}
    else:
        selected = {
            row["id"]
            for row in doc["records"]
            if row["partition"] == partition and row["id"] <= cutoff
        }
    if not selected:
        raise ValueError("question selects no visible record")
    receipts = _workflow_oracle(doc["records"])
    return _compact_workflow([row for row in receipts if row["job"] in selected])


def _tasks(doc: dict, world_id: str, oracle: list[dict], packaging: str = "joint") -> list[dict]:
    tasks = []
    split = packaging == "split"
    if packaging not in {"joint", "split"}:
        raise ValueError("unknown packaging mode")
    if split and doc["family"] != "workflow":
        raise ValueError("only the workflow family supports split packaging")
    key = "entity" if doc["family"] == "rule_learning" else "job"
    cutoffs = _cutoffs(doc) if doc["family"] == "workflow" else {}
    capability = RULE_LEARNING_CAPABILITY if key == "entity" else WORKFLOW_CAPABILITY
    for partition in range(doc["partitions"]):
        selected = {r["entity"] if key == "entity" else r["id"] for r in doc["records"] if r["partition"] == partition}
        prompt = prompt_for(doc["family"], packaging, partition)
        if split:
            # The row's context is the prefix ending at this partition's
            # cutoff, so the selected set is the partition slice inside it.
            cutoff = cutoffs[partition]
            question = {"partition": partition, "cutoff": cutoff}
            visible = {
                r["id"] for r in doc["records"]
                if r["partition"] == partition and r["id"] <= cutoff
            }
            answer = _compact_workflow([r for r in oracle if r[key] in visible])
        else:
            question = {"partition": partition}
            answer = ([r for r in oracle if r[key] in selected] if key == "entity"
                      else _compact_workflow([r for r in oracle if r[key] in selected]))
        tasks.append({"task_id": f"q{partition:03d}", "capability": capability,
                      "question": question, "prompt": prompt, "answer": answer,
                      "dependency_metadata": dependency_metadata(
                          doc["family"], packaging)})
    return tasks


def generate_bundle(seed: int, family: str = "rule_learning", n_records: int = 120,
                    n_questions: int = 16, topic: dict | None = None,
                    packaging: str | None = None) -> dict:
    if family not in {"rule_learning", "workflow"} or not 1 <= n_questions <= 16 or n_records < n_questions:
        raise ValueError("invalid family or record/question counts")
    packaging = packaging or DEFAULT_PACKAGING[family]
    if packaging not in {"joint", "split"}:
        raise ValueError("invalid packaging mode")
    if packaging == "split" and family != "workflow":
        raise ValueError("only the workflow family supports split packaging")
    rng = random.Random(seed)
    doc = {"family": family, "partitions": n_questions, "records": []}
    coefficients = None
    if family == "rule_learning":
        labels = [f"label_{rng.getrandbits(48):012x}" for _ in range(7)]
        coefficients = a, b, c = rng.randrange(1, 7), rng.randrange(1, 7), rng.randrange(7)
        doc.update({"modulus": 7, "labels": labels,
                    "protocol": PROTOCOLS["rule_learning"],
                    "demonstrations": [{"x": x, "y": y, "label": labels[(a*x+b*y+c) % 7]} for x, y in [(0, 0), (1, 0), (0, 1)]]})
        for i in range(n_records):
            doc["records"].append({"id": f"u{i:07d}", "partition": i % n_questions,
                                   "entity": f"e{i % (n_questions*3):04d}", "dx": rng.randrange(1, 7), "dy": rng.randrange(1, 7)})
        totals = {}
        last = {}
        for row in doc["records"]:
            x, y = totals.get(row["entity"], (0, 0))
            totals[row["entity"]] = ((x+row["dx"]) % 7, (y+row["dy"]) % 7)
            last[row["entity"]] = row
        for entity, (x, y) in totals.items():
            if (x, y) in {(0, 0), (1, 0), (0, 1)}:
                row = last[entity]
                row["dy"] = rng.choice([v for v in range(1, 7) if (x, (y+v-row["dy"]) % 7) not in {(0, 0), (1, 0), (0, 1)}])
    else:
        doc["protocol"] = PROTOCOLS["workflow"]
        schedule = (
            _split_partitions(n_records, n_questions)
            if packaging == "split"
            else [i * n_questions // n_records for i in range(n_records)]
        )
        for i in range(n_records):
            hidden = {"id": f"job{i:07d}", "payload": rng.randrange(1, 10000), "multiplier": rng.randrange(2, 10), "fault": bool(rng.randrange(2))}
            # Execute the first attempt to observe its status, then discard hidden fault.
            initial = execute_job(hidden)["trace"][1]
            doc["records"].append({k: v for k, v in hidden.items() if k != "fault"} | {
                "partition": schedule[i], "depends_on": f"job{i-1:07d}" if i else None,
                "observed_status": "checksum_error" if initial["after"] == "failed" else "processed"})
    changed = copy.deepcopy(doc)
    target = rng.randrange(n_records)
    field = "dx" if family == "rule_learning" else "payload"
    old = changed["records"][target][field]
    if family == "rule_learning":
        entity = changed["records"][target]["entity"]
        x = sum(r["dx"] for r in doc["records"] if r["entity"] == entity) % 7
        y = sum(r["dy"] for r in doc["records"] if r["entity"] == entity) % 7
        new = rng.choice([v for v in range(1, 7) if v != old and ((x+v-old) % 7, y) not in {(0, 0), (1, 0), (0, 1)}])
    else:
        new = old % 9999 + 1
    changed["records"][target][field] = new
    # Packaging is part of the world identity: a split bundle and a joint
    # bundle of the same seed publish different rows and must not collide.
    world_id = f"{family}-{seed}-{hashlib.sha256(_json([packaging, doc]).encode()).hexdigest()[:12]}"
    oracle = _rule_oracle(doc, coefficients) if coefficients else _workflow_oracle(doc["records"])
    cf_oracle = _rule_oracle(changed, coefficients) if coefficients else _workflow_oracle(changed["records"])
    lineage = {"source_kind": "simulated", "topic": topic, "schema": "rules-workflow-v3",
               "packaging": packaging, "package_schema": PACKAGING_SCHEMA,
               "answer_dependency_mode": dependency_metadata(family, packaging)["answer_dependency_mode"],
               "rule_family": "affine_mod7_induction" if coefficients else "dependent_checksum_repair_commit",
               "model_execution_measured": False, "strict_long_dependency": False}
    if coefficients:
        lineage["rule_structure_signature"] = hashlib.sha256(_json([*coefficients, 7]).encode()).hexdigest()
        lineage["rule_signature"] = hashlib.sha256(_json([*coefficients, doc["labels"]]).encode()).hexdigest()
        lineage["label_mapping_signature"] = hashlib.sha256(_json(doc["labels"]).encode()).hexdigest()
    else:
        lineage["workflow_policy_family"] = "dependent_checksum_repair_commit"
        # Offline replay under a fixed action table, stated positively: the
        # reader predicts the next action, it does not choose one.
        lineage["execution_mode"] = "offline_replay_with_fixed_policy"
        lineage["closed_loop"] = False
        lineage["fixed_policy"] = dict(_POLICY)
        lineage["execution_receipts"] = oracle
        lineage["counterfactual_execution_receipts"] = cf_oracle
    return {"world_id": world_id, "seed": seed, "family": family, "n_records": n_records, "n_questions": n_questions,
            "packaging": packaging, "context": _json(doc), "tasks": _tasks(doc, world_id, oracle, packaging),
            "lineage": lineage,
            "counterfactual": {"context": _json(changed), "tasks": _tasks(changed, world_id+"-cf", cf_oracle, packaging),
                               "intervention": {"record_id": doc["records"][target]["id"], "field": field, "before": old, "after": new}}}


def validate_bundle(bundle: dict) -> dict:
    errors, checks = [], {"answers_checked": 0, "changed_answers": 0}
    try:
        packaging = bundle.get("packaging", "joint")
        if packaging not in {"joint", "split"}:
            raise ValueError("invalid packaging mode")
        if packaging == "split" and bundle["family"] != "workflow":
            raise ValueError("only the workflow family supports split packaging")
        if bundle["lineage"].get("packaging", "joint") != packaging:
            errors.append("lineage packaging mismatch")
        docs = []
        for version in (bundle, bundle["counterfactual"]):
            doc = json.loads(version["context"])
            docs.append(doc)
            # The visible wording is part of the contract, not decoration:
            # solve_visible() reads only the structured fields, so a prompt or
            # protocol that contradicts the executor would otherwise still
            # validate. Pin both to the module constants.
            if doc.get("protocol") != PROTOCOLS[bundle["family"]]:
                errors.append("protocol does not match the registered contract")
            for task in version["tasks"]:
                if task.get("prompt") != prompt_for(bundle["family"], packaging, task["question"]["partition"]):
                    errors.append("task prompt does not match the registered contract")
                if task.get("capability") != capability_for(bundle["family"]):
                    errors.append("task capability tag does not match the registered contract")
            if len(doc["records"]) != bundle["n_records"] or len(version["tasks"]) != bundle["n_questions"]:
                errors.append("declared counts mismatch")
            if {t["question"]["partition"] for t in version["tasks"]} != set(range(bundle["n_questions"])):
                errors.append("partition coverage mismatch")
            if packaging == "split":
                expected = _split_partitions(bundle["n_records"], bundle["n_questions"])
                if [row["partition"] for row in doc["records"]] != expected:
                    errors.append("split partition schedule mismatch")
            for task in version["tasks"]:
                if packaging == "split" and task["question"].get("cutoff") != _cutoffs(doc)[task["question"]["partition"]]:
                    errors.append("question cutoff mismatch")
            for row in doc["records"]:
                if "fault" in row or "value" in row or "continuation" in row:
                    errors.append("hidden field leaked")
                if bundle["family"] == "rule_learning" and not (1 <= row["dx"] <= 6 and 1 <= row["dy"] <= 6):
                    errors.append("out of domain increment")
            for task in version["tasks"]:
                if solve_visible(version["context"], task["question"]) != task["answer"]:
                    errors.append("answer mismatch")
                checks["answers_checked"] += 1
        expected = copy.deepcopy(docs[0])
        intervention = bundle["counterfactual"]["intervention"]
        row = next(r for r in expected["records"] if r["id"] == intervention["record_id"])
        if row[intervention["field"]] != intervention["before"]:
            errors.append("intervention before mismatch")
        row[intervention["field"]] = intervention["after"]
        if expected != docs[1]:
            errors.append("undeclared counterfactual changes")
        checks["changed_answers"] = sum(a["answer"] != b["answer"] for a, b in zip(bundle["tasks"], bundle["counterfactual"]["tasks"]))
        if not checks["changed_answers"]:
            errors.append("intervention did not affect answers")
        if bundle["family"] == "workflow":
            for doc, key in zip(docs, ("execution_receipts", "counterfactual_execution_receipts")):
                if _workflow_oracle(doc["records"]) != bundle["lineage"].get(key):
                    errors.append("execution receipt mismatch")
    except (ValueError, KeyError, TypeError, StopIteration) as exc:
        errors.append(str(exc))
    return {"passed": not errors, "errors": errors, "checks": checks}
