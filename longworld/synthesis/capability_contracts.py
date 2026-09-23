"""P73 answer contracts: what the assistant message must contain.

The P72 diagnosis found ~73% of supervised tokens are id enumeration
(matched/records/pairs lists) — provenance the task never asked the user for.
Training a model to enumerate every consumed id plausibly teaches "output every
related object", which is exactly the GraphWalks failure mode (superset
answers at 7x the gold size). This module splits the supervised answer into
explicit contracts:

- full-provenance: the answer as generated (gold lists included);
- answer-only: only what the question asks — the aggregate scalars, the
  verdict, the named entities' totals; no id lists unless ids ARE the answer;
- answer+minimal-evidence: answer-only plus compact counts (sizes, counts of
  members), never id enumerations.

Instruction and answer must agree: an answer-only row's user message is
re-rendered to require only the final result, otherwise the model is trained
to ignore its own instruction. The records PROTOCOLS text promises "matched
ids and the breakdown", so the instruction swap is not cosmetic.

Design doc: .hl/design/p73_counterexample_synthesis.md §3.3.
"""

from __future__ import annotations

import json
from typing import Any

CONTRACTS = ("full-provenance", "answer-only", "answer+minimal-evidence")

# Families whose answer IS an id list: enumeration is the requested output,
# not excess provenance, so answer-only keeps it.
IDS_ARE_ANSWER = ("alias_locate",)

ANSWER_ONLY_PROMPTS = {
    "filter_aggregate": "Run the filter-and-aggregate program and report only the final aggregate value.",
    "group_compare": "Run the group-compare program and report only the two group values and the verdict.",
    "join_lookup": "Run the join-and-aggregate program and report only the aggregate value and the per-entity totals.",
}

# Replacement Return sentences matching the records RETURNS style, narrowed to
# the answer-only key sets (see project_answer).
ANSWER_ONLY_RETURNS = {
    "filter_aggregate": "Return one JSON object with keys aggregate (how, value).",
    "group_compare": "Return one JSON object with keys groups (category to value) and verdict (GT, LT or EQ).",
    "join_lookup": "Return one JSON object with keys aggregate (how, value) and by_entity (entity to total).",
}


def contract_answer(family: str, answer: Any) -> tuple[Any, str]:
    """(answer, contract) for a full-provenance row: verbatim."""
    if family not in _KNOWN:
        raise ValueError(f"unsupported family: {family}")
    return answer, "full-provenance"


_KNOWN = (
    "filter_aggregate",
    "group_compare",
    "join_lookup",
    "alias_locate",
    "asof_state",
    "rule_holdout",
    "join_unanswerable",
    "set_complete",
    "dense_aggregate",
    "research_run",
    "research_join",
)


def project_answer(family: str, answer: Any, contract: str) -> Any:
    """The assistant content under a contract. Fails loud on unknown shapes.

    `full-provenance` returns the answer untouched. The other contracts
    project: ids are kept only where they are the asked-for answer
    (alias_locate) or the per-entity mapping (join totals, asof balances).
    """
    if contract == "full-provenance":
        return answer
    if family in IDS_ARE_ANSWER:
        return answer
    if not isinstance(answer, dict):
        # "UNKNOWN" (join_unanswerable) and bare lists pass through.
        return answer
    if family == "filter_aggregate":
        return {"aggregate": answer["aggregate"]}
    if family == "group_compare":
        return {"groups": answer["groups"], "verdict": answer["verdict"]}
    if family == "join_lookup":
        return {"aggregate": answer["aggregate"], "by_entity": answer["by_entity"]}
    if family == "asof_state":
        out = {k: v for k, v in answer.items() if k not in ("entities",)}
        out["entities"] = answer.get("entities")
        return out
    if family == "rule_holdout":
        return {k: v for k, v in answer.items() if k != "features"}
    if family in ("set_complete", "dense_aggregate", "research_run", "research_join"):
        # These families' answers are lists of members / chains / counts -- the
        # membership is the answer, keep verbatim; minimal-evidence adds counts.
        if contract == "answer+minimal-evidence" and isinstance(answer, dict):
            return _with_counts(answer)
        return answer
    raise ValueError(f"unsupported family: {family}")


def _with_counts(answer: dict[str, Any]) -> dict[str, Any]:
    """Compact evidence: replace id lists with their lengths, keep scalars."""
    out: dict[str, Any] = {}
    for key, value in sorted(answer.items()):
        if isinstance(value, list) and value and isinstance(value[0], str):
            out[f"{key}_count"] = len(value)
        elif isinstance(value, dict):
            out[key] = {
                k: (f"{len(v)}" if isinstance(v, list) else v)
                for k, v in sorted(value.items())
            }
        else:
            out[key] = value
    return out


def minimal_evidence(family: str, answer: Any) -> Any:
    """answer-only plus compact counts, never id enumerations."""
    base = project_answer(family, answer, "answer-only")
    if isinstance(base, dict):
        return _with_counts(base)
    return base


def instruction_for_contract(family: str, instruction: str, contract: str) -> str:
    """Re-render the instruction when the contract narrows the output.

    Only records families have RETURNS text promising id lists; their
    answer-only phrasing swaps the task sentence and the Return sentence.
    Other families keep the original instruction: their answers carry no
    excess enumeration under any contract (except ids-are-answer, which is
    verbatim). The instruction here is the QUESTION tail only -- the world
    context must stay attached; see user_message_for_contract.
    """
    if contract == "full-provenance":
        return instruction
    if contract == "answer+minimal-evidence":
        return instruction
    if family in ANSWER_ONLY_PROMPTS:
        return _rerender_records_instruction(family, instruction)
    return instruction


def _rerender_records_instruction(family: str, instruction: str) -> str:
    """Swap the task and Return sentences of a records instruction.

    Layout (render_instruction): "Task: <t> Fields: <f> Program: <p> <returns>".
    The program text is the task definition and stays; the task sentence and
    the Return sentence are what promise provenance, so only they change.
    """
    fields_at = instruction.find(" Fields: ")
    program_at = instruction.find(" Program: ", fields_at)
    returns_at = instruction.find("Return one JSON object", program_at)
    if fields_at == -1 or program_at == -1 or returns_at == -1:
        raise ValueError(
            f"records instruction not in render_instruction layout: {instruction[:120]!r}"
        )
    fields = instruction[fields_at:program_at]
    program = instruction[program_at:returns_at]
    return (
        f"Task: {ANSWER_ONLY_PROMPTS[family]}{fields}{program}"
        f"{ANSWER_ONLY_RETURNS[family]}"
    )


def user_message_for_contract(family: str, user: str, contract: str) -> str:
    """The user message under a contract: context preserved, instruction swapped.

    The pipeline renders the user message as context + "\\n\\nQUESTION\\n" +
    instruction; the context is the data the model needs and must survive
    every contract. Answer-only swaps only the instruction (see
    instruction_for_contract).
    """
    context, sep, instruction = user.partition("\n\nQUESTION\n")
    if not sep:
        raise ValueError("user message has no QUESTION separator")
    if context and instruction:
        instruction = instruction_for_contract(family, instruction, contract)
    return context + sep + instruction


def render_answer(answer: Any) -> str:
    """Canonical serialization, matching the bank's `canonical` convention."""
    return json.dumps(answer, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
