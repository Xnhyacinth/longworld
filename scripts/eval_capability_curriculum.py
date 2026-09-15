#!/usr/bin/env python3
"""Evaluate curriculum QA maps; keep host, format, and truncation outcomes separate."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


def grade_response(response: dict, gold: dict) -> dict:
    try:
        choice = response["choices"][0]
        finish = choice["finish_reason"]
        content = choice["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("response content is not text")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return {"status": "protocol_error", "error": str(exc)}
    if finish == "length":
        return {"status": "truncated", "finish_reason": finish}
    if finish != "stop":
        return {"status": "unfinished", "finish_reason": finish}
    try:
        prediction = _strict_json(content)
        if not isinstance(prediction, dict):
            raise TypeError("prediction must be a JSON object")
    except (ValueError, TypeError) as exc:
        return {"status": "invalid_format", "error": str(exc), "qa_correct": 0,
                "qa_total": len(gold), "row_correct": False}
    # Canonical JSON comparison prevents Python's True == 1 equivalence.
    matches = {key: key in prediction and canonical(prediction[key]) == canonical(value)
               for key, value in gold.items()}
    return {"status": "answered", "qa_correct": sum(matches.values()), "qa_total": len(gold),
            "per_qa": matches, "row_correct": all(matches.values()) and set(prediction) == set(gold),
            "unexpected_keys": sorted(set(prediction) - set(gold))}


def summarize(records: list[dict]) -> dict:
    completed = [r for r in records if r["status"] in {"answered", "invalid_format"}]
    qa_total = sum(r["qa_total"] for r in completed)
    groups = defaultdict(dict)
    for row in completed:
        # Same world at different capacity must never form a cross-capacity pair.
        key = (row["world_id"], row.get("capacity_tokens"))
        if row["view"] in groups[key]:
            raise ValueError("duplicate completed world/view/capacity")
        groups[key][row["view"]] = row
    pairs = [pair for pair in groups.values() if {"factual", "counterfactual"} <= set(pair)]
    return {"status_counts": dict(Counter(r["status"] for r in records)),
            "completed_rows": len(completed), "qa_total_completed": qa_total,
            "qa_exact_accuracy_completed": sum(r["qa_correct"] for r in completed) / qa_total if qa_total else None,
            "row_exact_accuracy_completed": sum(r["row_correct"] for r in completed) / len(completed) if completed else None,
            "complete_pairs": len(pairs),
            "paired_row_exact_accuracy": sum(p["factual"]["row_correct"] and p["counterfactual"]["row_correct"] for p in pairs) / len(pairs) if pairs else None,
            "accuracy_denominator": "stop-completed responses; invalid JSON counts as format failure and incorrect; transport/protocol/truncated/unfinished excluded"}


def evaluate_rows(rows: list[dict], transport: Callable[[dict], dict], model: str,
                  max_output_tokens: int = 4096) -> tuple[list[dict], dict]:
    records, seen = [], set()
    for row in rows:
        example_id = row["example_id"]
        if example_id in seen:
            raise ValueError("duplicate example_id")
        seen.add(example_id)
        messages = row["messages"]
        if len(messages) < 2 or messages[-1]["role"] != "assistant":
            raise ValueError("expected final assistant gold message")
        gold = _strict_json(messages[-1]["content"])
        if not isinstance(gold, dict) or not gold:
            raise ValueError("gold must be a nonempty QA map")
        request = {"model": model, "messages": messages[:-1], "temperature": 0,
                   "max_tokens": max_output_tokens}
        record = {"example_id": example_id, "world_id": row["world_id"], "view": row["view"],
                  "capacity_tokens": row.get("capacity_tokens"), "model_requested": model,
                  "request_sha256": hashlib.sha256(canonical(request).encode()).hexdigest(),
                  "input_sha256": hashlib.sha256(canonical(messages[:-1]).encode()).hexdigest()}
        try:
            response = transport(request)
            record.update(grade_response(response, gold))
            record["raw_response"] = response
            record["model_returned"] = response.get("model") if isinstance(response, dict) else None
        except (OSError, TimeoutError, URLError) as exc:
            record.update(status="transport_error", error_type=type(exc).__name__, error=str(exc))
        records.append(record)
    return records, summarize(records)


def endpoint_transport(base_url: str, timeout: float) -> Callable[[dict], dict]:
    endpoint = base_url.rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions" if endpoint.endswith("/v1") else "/v1/chat/completions"

    def call(payload):
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("OPENAI_API_KEY")
        if token:
            headers["Authorization"] = "Bearer " + token
        request = Request(endpoint, data=canonical(payload).encode(), headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8")
        except HTTPError as exc:
            # Do not persist server headers or credentials.
            raise OSError(f"HTTP {exc.code}: {exc.reason}") from exc
        try:
            return _strict_json(text)
        except ValueError:
            return {"invalid_http_response_body": text}

    return call


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-rows", type=int, default=4)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--responses", type=Path, help="Offline JSONL: example_id and raw_response; never labeled endpoint measurement")
    args = parser.parse_args()
    if args.max_rows < 1 or args.max_output_tokens < 1 or args.timeout <= 0:
        parser.error("row/token limits and timeout must be positive")
    if bool(args.responses) == bool(args.base_url):
        parser.error("provide exactly one of --base-url or --responses")
    rows = []
    with args.input.open() as stream:
        for line in stream:
            if line.strip():
                rows.append(json.loads(line))
            if len(rows) >= args.max_rows:
                break
    if args.responses:
        responses = {}
        for line in args.responses.read_text().splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry["example_id"] in responses:
                raise ValueError("duplicate offline response")
            responses[entry["example_id"]] = entry["raw_response"]
        ordered = iter(rows)

        def transport(_request):
            row = next(ordered)
            if row["example_id"] not in responses:
                raise OSError("offline response missing")
            return responses[row["example_id"]]
    else:
        transport = endpoint_transport(args.base_url, args.timeout)
    args.output.mkdir(parents=True, exist_ok=False)
    records, summary = evaluate_rows(rows, transport, args.model, args.max_output_tokens)
    summary.update(mode="offline_scoring" if args.responses else "endpoint_evaluation",
                   endpoint_called=not bool(args.responses), model_requested=args.model,
                   input_path=str(args.input), selected_rows=len(rows))
    with (args.output / "responses.jsonl").open("w") as stream:
        for record in records:
            stream.write(canonical(record) + "\n")
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
