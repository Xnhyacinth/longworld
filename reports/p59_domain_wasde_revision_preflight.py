"""Execute a frozen WASDE revision path and reject source/window shortcuts."""
import hashlib
import json
import re
from pathlib import Path

from tokenizers import Tokenizer


FIELDS = {
    "beginning_stocks": "Beginning Stocks",
    "production": "Production",
    "imports": "Imports",
    "supply": "Supply, Total",
    "domestic_use": "Domestic, Total",
    "exports": "Exports",
    "use": "Use, Total",
    "ending_stocks": "Ending Stocks",
}


def parse_corn(text, months):
    sections = list(re.finditer(r"WASDE - \d+ - 12\s+[^\r\n]+", text))
    if len(sections) != 1:
        raise ValueError("missing or ambiguous U.S. corn source page")
    start = sections[0].start()
    next_page = re.search(r"WASDE - \d+ - 13\s+", text[start:])
    if next_page is None:
        raise ValueError("source page end missing")
    section = text[start:start + next_page.start()]
    if "U.S. Feed Grain and Corn Supply and Use" not in section:
        raise ValueError("wrong commodity/geography")
    if section.count("2025/26 Proj.") != 2:
        raise ValueError("wrong marketing-year columns")
    if not re.search(r"Item\s+" + months[0] + r"\s+" + months[1] + r"\s", section):
        raise ValueError("wrong vintage columns")
    corn_start = section.index("\nCORN")
    corn = section[corn_start:]
    if "Million Bushels" not in corn:
        raise ValueError("wrong units")
    records = {month: {} for month in months}
    spans = []
    for role, label in FIELDS.items():
        matches = list(re.finditer(r"(?m)^\s*" + re.escape(label) + r"\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)\s*\r?$", corn))
        if len(matches) != 1:
            raise ValueError("missing or ambiguous operand: " + role)
        row = matches[0]
        for index, month in enumerate(months, 3):
            records[month][role] = int(row[index].replace(",", ""))
            left = start + corn_start + row.start(index)
            right = start + corn_start + row.end(index)
            if text[left:right] != row[index]:
                raise ValueError("operand span mismatch")
            spans.append({"vintage": month, "role": role, "start": left, "end": right, "quote": row[index]})
    return records, spans, section


def evaluate(parsed_sources, months):
    values = {}
    for source in parsed_sources.values():
        for month, fields in source.items():
            if month in values and values[month] != fields:
                raise ValueError("duplicated vintage disagrees across publications")
            values[month] = fields
    if not all(month in values for month in months):
        return None
    for month in months:
        row = values[month]
        if row["beginning_stocks"] + row["production"] + row["imports"] != row["supply"]:
            raise ValueError("reported supply identity does not reconcile: " + month)
        if row["domestic_use"] + row["exports"] != row["use"]:
            raise ValueError("reported use identity does not reconcile: " + month)
        if row["supply"] - row["use"] != row["ending_stocks"]:
            raise ValueError("reported ending-stock identity does not reconcile: " + month)
    changes = []
    for old, new in zip(months, months[1:]):
        a, b = values[old], values[new]
        contributions = {role: (b[role] - a[role]) * (-1 if role in {"domestic_use", "exports"} else 1) for role in ("beginning_stocks", "production", "imports", "domestic_use", "exports")}
        delta = b["ending_stocks"] - a["ending_stocks"]
        assert sum(contributions.values()) == delta
        changes.append({"from": old, "to": new, "ending_stock_revision": delta, "contributions": contributions})
    return {"unit": "Million Bushels", "changes": changes, "total_absolute_revision": sum(abs(row["ending_stock_revision"]) for row in changes), "net_revision": values[months[-1]]["ending_stocks"] - values[months[0]]["ending_stocks"]}


def main():
    config = json.loads(Path("configs/p59_domain_wasde_revision_v1.json").read_text())
    root = Path(config["data_root"])
    assert hashlib.sha256(Path(config["tokenizer"]).read_bytes()).hexdigest() == config["tokenizer_sha256"]
    assert hashlib.sha256((root / config["rights"]["file"]).read_bytes()).hexdigest() == config["rights"]["sha256"]
    tokenizer = Tokenizer.from_file(config["tokenizer"])

    def count(text):
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    parsed, texts, full_texts, spans_by_source, source_receipts = {}, {}, {}, {}, []
    for source in config["sources"]:
        raw = (root / source["file"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == source["sha256"]
        assert len(raw) == source["bytes"]
        text = raw.decode("utf-8")
        records, spans, section = parse_corn(text, source["projection_months"])
        parsed[source["file"]] = records
        texts[source["file"]] = section
        full_texts[source["file"]] = text
        spans_by_source[source["file"]] = spans
        source_receipts.append({**source, "raw_tokens": count(text), "corn_page_tokens": count(section), "records": records, "source_spans": spans})
    months = config["target"]["vintages"]
    selected = {name: parsed[name] for name in config["trajectory_sources"]}
    extension = config["rejected_extension"]
    try:
        evaluate({name: parsed[name] for name in extension["sources"]}, extension["vintages"])
    except ValueError as error:
        extension_failure = str(error)
    else:
        raise AssertionError("the documented September identity failure changed")
    answer = evaluate(selected, months)
    assert answer is not None
    removals = {name: evaluate({key: value for key, value in selected.items() if key != name}, months) for name in selected}
    assert all(value is None for value in removals.values())
    assert evaluate({}, months) is None
    assert evaluate({"latest": parsed["wasde0925.txt"]}, months) is None
    # The adjacent-month task is answered by July alone because it reprints June.
    adjacent = evaluate({name: parsed[name] for name in config["adjacent_pair_sources"]}, ["Jun", "Jul"])
    assert evaluate({"july": parsed["wasde0725.txt"]}, ["Jun", "Jul"]) == adjacent
    interventions = {}
    for month in months:
        twin = {}
        edits = []
        for name in selected:
            text = full_texts[name]
            selected_spans = [span for span in spans_by_source[name] if span["vintage"] == month and span["role"] in {"production", "supply", "ending_stocks"}]
            for span in sorted(selected_spans, key=lambda value: value["start"], reverse=True):
                assert text[span["start"]:span["end"]] == span["quote"]
                replacement = str(int(span["quote"].replace(",", "")) + 1)
                text = text[:span["start"]] + replacement + text[span["end"]:]
                edits.append({"source": name, **span, "replacement": replacement})
            source_months = next(source["projection_months"] for source in config["sources"] if source["file"] == name)
            twin[name] = parse_corn(text, source_months)[0]
        changed = evaluate(twin, months)
        assert changed != answer
        interventions[month] = {"source_origin": "synthetic_counterfactual", "operation": "add_one_to_production_supply_and_ending_stock_consistently", "source_span_edits": edits, "answer_changes": True}
    witness = "\n".join(texts[name] for name in selected)
    receipt = {"schema_version": config["schema_version"], "answer_program_id": config["answer_program_id"], "sources": source_receipts, "answer": answer, "source_removal_answers": removals, "empty_source_answer": None, "latest_only_answer": None, "adjacent_pair_remove_june_answer_unchanged": True, "vintage_interventions": interventions, "rejected_extension": {**extension, "observed_error": extension_failure}, "sparse_complete_page_witness_tokens": count(witness), "single_page_tokens": {name: count(texts[name]) for name in selected}, "status": "integration_preflight_only", "shared_candidates": 0, "dense_audited_rows": 0, "training_inventory_delta": 0, "limitations": "No shared adapter, signed task binding, full raw-window enumeration or near-duplicate audit. Sparse-page witness demonstrates integration/retrieval solvability; it is not itself a contiguous-window witness over the full source pack. No full reports packed for length."}
    (root / "revision_preflight_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    candidate = {"question": config["question"], "answer": answer, "answer_program_id": config["answer_program_id"], "context": witness, "train_ready": False, "production_eligible": False, "profile": "integration_diagnostic"}
    (root / "integration_diagnostic_candidate.jsonl").write_text(json.dumps(candidate) + "\n")
    print(json.dumps({"answer": answer, "sparse_page_tokens": count(witness), "removals": removals, "inventory_delta": 0}, indent=2))


if __name__ == "__main__":
    main()
