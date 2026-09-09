"""Reproduce a source-only preflight; emits no shared training candidates."""
import hashlib
import json
import re
from pathlib import Path

from pypdf import PdfReader
from tokenizers import Tokenizer


def main():
    config = json.loads(Path("configs/p58_domain_nasa_caib_preflight_v1.json").read_text())
    root = Path(config["data_root"])
    for source in config["sources"]:
        raw = (root / source["file"]).read_bytes()
        assert len(raw) == source["bytes"]
        assert hashlib.sha256(raw).hexdigest() == source["sha256"]
    assert hashlib.sha256(Path(config["tokenizer"]).read_bytes()).hexdigest() == config["tokenizer_sha256"]
    tokenizer = Tokenizer.from_file(config["tokenizer"])

    def count(text):
        return len(tokenizer.encode(text, add_special_tokens=False).ids)

    def normalized(text):
        return re.sub(r"\s+", " ", text).strip()

    pages = {}
    capacities = {}
    for name in ("volume1.pdf", "ntrs_volume1.pdf", "report.pdf", "rtf_final.pdf"):
        values = [page.extract_text() or "" for page in PdfReader(root / name).pages]
        pages[name] = values
        lines = list(dict.fromkeys(line.strip() for text in values for line in text.splitlines() if line.strip()))
        capacities[name] = {
            "pages": len(values),
            "raw_joined_tokens": count("\n\f\n".join(values)),
            "page_sum_tokens": sum(count(text) for text in values),
            "exact_unique_nonempty_line_tokens": count("\n".join(lines)),
            "pages_under_300_extracted_characters": sum(len(text) < 300 for text in values),
            "eligible_proof_capacity": None,
            "near_dedup_and_layout_audit": "not run",
        }
        (root / (name + ".pages.json")).write_text(json.dumps(values))

    caib = pages["ntrs_volume1.pdf"]
    rtf = pages["rtf_final.pdf"]
    physical = normalized(caib[8])
    physical_match = re.search(r"External Tank at ([0-9.]+) seconds.*?panel number (\d+)", physical)
    assert physical_match is not None
    chapter11 = "\n".join(caib[224:227])
    recommendations = re.findall(r"\bR\d+\.\d+-\d+\b", chapter11)
    assert len(set(recommendations)) == 29
    section = normalized("\n".join(rtf[32:40]))

    def assessment(text):
        requirement = re.search(r"Initiate an aggressive program.*?attach to the External Tank\.", text)
        majority = re.search(r"intent of CAIB Recommendation (3\.2-1) has (not been met)\.", text)
        minority = re.search(r"Technical Panel believes that NASA (met the intent) of CAIB recommendation (3\.2-1)", text)
        date = re.search(r"assessment of NASA’s actions was completed at the (June 27, 2005) meeting", text)
        if not all((requirement, majority, minority, date)):
            return None
        return {"id": majority[1], "required_action": requirement[0], "majority": majority[2], "minority": minority[1], "decision_date": date[1]}

    answer = assessment(section)
    assert answer is not None
    full = normalized("\n".join(caib + rtf))
    full_answer = assessment(full)
    assert full_answer is not None
    # PDF line-end hyphenation differs between the original and quoted requirement.
    for key in answer:
        if key == "required_action":
            assert re.sub(r"[^a-z]", "", full_answer[key].lower()) == re.sub(r"[^a-z]", "", answer[key].lower())
        else:
            assert full_answer[key] == answer[key]
    assert assessment(normalized("\n".join(rtf))) == answer
    assert assessment(normalized("\n".join(caib))) is None
    assert assessment("") is None
    rights = {}
    for filename in ("ntrs_metadata.json", "rtf_metadata.json"):
        metadata = json.loads((root / filename).read_text())
        rights[filename] = {"distribution": metadata["distribution"], "copyright": metadata["copyright"], "modified": metadata["modified"]}
    diagnostics = [
        {"task": "physical_cause_timing_and_panel", "answer": {"launch_seconds": physical_match[1], "panel": physical_match[2]}, "source": "ntrs_volume1.pdf", "pdf_pages": [9], "contiguous_raw_tokens": count(caib[8]), "classification": "short_window_answerable"},
        {"task": "all_recommendation_identifiers", "answer": sorted(set(recommendations)), "source": "ntrs_volume1.pdf", "pdf_pages": [225, 226, 227], "contiguous_raw_tokens": count(chapter11), "classification": "short_window_answerable"},
        {"task": "original_requirement_majority_minority_assessment", "answer": answer, "source": "rtf_final.pdf", "pdf_pages": list(range(33, 41)), "contiguous_raw_tokens": count("\n".join(rtf[32:40])), "classification": "short_window_answerable", "remove_original_caib_answer_unchanged": True, "remove_rtf_answer_unavailable": True, "empty_source_answer_unavailable": True},
    ]
    for item in diagnostics:
        item["raw_window_witnesses"] = {str(window): item["contiguous_raw_tokens"] <= window for window in (4000, 8000, 16000)}
    receipt = {
        "schema_version": config["schema_version"],
        "sources": config["sources"],
        "tokenizer_sha256": config["tokenizer_sha256"],
        "capacities": capacities,
        "ntrs_and_nasa_volume1_page_texts_equal": pages["ntrs_volume1.pdf"] == pages["volume1.pdf"],
        "rights": rights,
        "diagnostic_tasks": diagnostics,
        "shared_candidates": 0,
        "dense_audited_rows": 0,
        "valid_strict_rows": 0,
        "valid_retrieval_rows": 0,
        "training_inventory_delta": 0,
        "decision": "REJECT_STRICT_CURRENT_ORACLES; retrieval recipes require shared adapter and audit",
        "limits": "Positive source witnesses suffice to reject strict. This is not exhaustive shared raw-window enumeration, dense replay, near-dedup, a signed source manifest, or production approval. Volume III OCR gaps and per-source rights remain unresolved; it is excluded from eligible capacity.",
    }
    (root / "preflight_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    (root / "retrieval_diagnostic_recipes.jsonl").write_text("\n".join(json.dumps(row) for row in diagnostics) + "\n")
    print(json.dumps({"capacities": capacities, "diagnostics": [{"task": row["task"], "tokens": row["contiguous_raw_tokens"]} for row in diagnostics], "decision": receipt["decision"]}, indent=2))


if __name__ == "__main__":
    main()
