"""Finite-rule induction and bounded causal workflow replay, with separate oracles."""
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


def execute_job(job: dict) -> dict:
    job = {**job, "value": job.get("value", job["payload"] * job["multiplier"])}
    state, trace = "new", []
    policy = {"new": "inspect", "ready": "process", "failed": "repair", "repaired": "process", "processed": "commit"}
    while state != "committed":
        receipt = step_job(job, state, policy[state])
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


def _tasks(doc: dict, world_id: str, oracle: list[dict]) -> list[dict]:
    tasks = []
    key = "entity" if doc["family"] == "rule_learning" else "job"
    for partition in range(doc["partitions"]):
        selected = {r["entity"] if key == "entity" else r["id"] for r in doc["records"] if r["partition"] == partition}
        prompt = PROMPTS[doc["family"]]
        tasks.append({"task_id": f"q{partition:03d}",
                      "capability": "L4_rule_induction" if key == "entity" else "L5_bounded_causal_replay",
                      "question": {"partition": partition}, "prompt": prompt,
                      "answer": ([r for r in oracle if r[key] in selected] if key == "entity" else _compact_workflow([r for r in oracle if r[key] in selected]))})
    return tasks


def generate_bundle(seed: int, family: str = "rule_learning", n_records: int = 120,
                    n_questions: int = 16, topic: dict | None = None) -> dict:
    if family not in {"rule_learning", "workflow"} or not 1 <= n_questions <= 16 or n_records < n_questions:
        raise ValueError("invalid family or record/question counts")
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
        for i in range(n_records):
            hidden = {"id": f"job{i:07d}", "payload": rng.randrange(1, 10000), "multiplier": rng.randrange(2, 10), "fault": bool(rng.randrange(2))}
            # Execute the first attempt to observe its status, then discard hidden fault.
            initial = execute_job(hidden)["trace"][1]
            doc["records"].append({k: v for k, v in hidden.items() if k != "fault"} | {
                "partition": i * n_questions // n_records, "depends_on": f"job{i-1:07d}" if i else None,
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
    world_id = f"{family}-{seed}-{hashlib.sha256(_json(doc).encode()).hexdigest()[:12]}"
    oracle = _rule_oracle(doc, coefficients) if coefficients else _workflow_oracle(doc["records"])
    cf_oracle = _rule_oracle(changed, coefficients) if coefficients else _workflow_oracle(changed["records"])
    lineage = {"source_kind": "simulated", "topic": topic, "schema": "rules-workflow-v2",
               "rule_family": "affine_mod7_induction" if coefficients else "dependent_checksum_repair_commit",
               "model_execution_measured": False, "strict_long_dependency": False}
    if coefficients:
        lineage["rule_structure_signature"] = hashlib.sha256(_json([*coefficients, 7]).encode()).hexdigest()
        lineage["rule_signature"] = hashlib.sha256(_json([*coefficients, doc["labels"]]).encode()).hexdigest()
        lineage["label_mapping_signature"] = hashlib.sha256(_json(doc["labels"]).encode()).hexdigest()
    else:
        lineage["workflow_policy_family"] = "dependent_checksum_repair_commit"
        lineage["execution_receipts"] = oracle
        lineage["counterfactual_execution_receipts"] = cf_oracle
    return {"world_id": world_id, "seed": seed, "family": family, "n_records": n_records, "n_questions": n_questions,
            "context": _json(doc), "tasks": _tasks(doc, world_id, oracle), "lineage": lineage,
            "counterfactual": {"context": _json(changed), "tasks": _tasks(changed, world_id+"-cf", cf_oracle),
                               "intervention": {"record_id": doc["records"][target]["id"], "field": field, "before": old, "after": new}}}


def validate_bundle(bundle: dict) -> dict:
    errors, checks = [], {"answers_checked": 0, "changed_answers": 0}
    try:
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
                if task.get("prompt") != PROMPTS[bundle["family"]]:
                    errors.append("task prompt does not match the registered contract")
            if len(doc["records"]) != bundle["n_records"] or len(version["tasks"]) != bundle["n_questions"]:
                errors.append("declared counts mismatch")
            if {t["question"]["partition"] for t in version["tasks"]} != set(range(bundle["n_questions"])):
                errors.append("partition coverage mismatch")
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
