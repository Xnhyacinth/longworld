"""Exact JSON-window counts at the pinned Qwen runtime's separator boundaries."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from longworld.core.attestation import sanitized_attestation_environment
from longworld.core.pack import SEP

_PATTERN = (
    r"(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\r\n\p{L}\p{N}]?\p{L}+|\p{N}|"
    r" ?[^\s\p{L}\p{N}]+[\r\n]*|\s*[\r\n]+|\s+(?!\S)|\s+"
)
_PRE_TOKENIZER = {
    "type": "Sequence",
    "pretokenizers": [
        {
            "type": "Split",
            "pattern": {"Regex": _PATTERN},
            "behavior": "Isolated",
            "invert": False,
        },
        {
            "type": "ByteLevel",
            "add_prefix_space": False,
            "trim_offsets": True,
            "use_regex": False,
        },
    ],
}
_POST_PROCESSOR = {
    "type": "ByteLevel",
    "add_prefix_space": False,
    "trim_offsets": False,
    "use_regex": False,
}
_MODEL = {
    "type": "BPE",
    "dropout": None,
    "unk_token": None,
    "continuing_subword_prefix": "",
    "end_of_word_suffix": "",
    "fuse_unk": False,
    "byte_fallback": False,
    "ignore_merges": False,
}


@dataclass(frozen=True)
class JsonWindowTokenCounter:
    """Counts only complete document intervals; it supplies no token offsets."""

    prefix_with_separator: tuple[int, ...]
    final_document_tokens: tuple[int, ...]
    token_counter: Callable[[str], int]
    tokenizer: Any
    backend: Any
    backend_state: str

    def __call__(self, start: int, stop: int) -> int:
        if not 0 <= start < stop <= len(self.final_document_tokens):
            raise IndexError("JSON token window is outside its document pool")
        return (
            self.prefix_with_separator[stop - 1]
            - self.prefix_with_separator[start]
            + self.final_document_tokens[stop - 1]
        )

    def verify_unchanged(self) -> None:
        serialize = getattr(self.backend, "to_str", None)
        if (
            getattr(self.token_counter, "_json_window_tokenizer", None)
            is not self.tokenizer
            or getattr(self.tokenizer, "backend_tokenizer", None) is not self.backend
            or not callable(serialize)
            or serialize() != self.backend_state
        ):
            raise ValueError("tokenizer changed during exact JSON-window proof")


def certified_json_window_counter(
    documents: list[str], token_counter: Callable[[str], int]
) -> JsonWindowTokenCounter | None:
    """Admit a separator-additive counter or retain ordinary exact encoding.

    Only the source-sidecar loader grants the plain-encode capability. NFC cannot
    compose across the ASCII object/separator boundaries. In this exact runtime
    regex, SEP's trailing newlines end a pre-token before the next ``{``; there
    is no left-context assertion. Deterministic BPE operates within pre-tokens,
    and the ByteLevel postprocessor does not alter IDs. Thus an interval is the
    concatenation of encoded ``doc + SEP`` pieces and one encoded final doc.

    The full-ID comparison checks the observed instance in addition to these
    structural conditions; it does not authorize other tokenizers. Raw-window
    offsets and every window replay still use the original audit paths.
    """
    tokenizer = getattr(token_counter, "_json_window_tokenizer", None)
    if tokenizer is None:
        return None
    backend = getattr(tokenizer, "backend_tokenizer", None)
    if backend is None or not documents or SEP != "\n\n===== DOCUMENT =====\n\n":
        return None
    for document in documents:
        if (
            not document.startswith("{")
            or not document.endswith("}")
            or SEP in document
        ):
            return None
        try:
            value = json.loads(document)
        except ValueError:
            return None
        if not isinstance(value, dict) or document != json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ):
            return None
    backend_state = backend.to_str()
    state = json.loads(backend_state)
    model = state.get("model")
    if (
        state.get("normalizer") != {"type": "NFC"}
        or state.get("pre_tokenizer") != _PRE_TOKENIZER
        or state.get("post_processor") != _POST_PROCESSOR
        or state.get("padding") is not None
        or state.get("truncation") is not None
        or not isinstance(model, dict)
        or {
            key: value for key, value in model.items() if key not in {"vocab", "merges"}
        }
        != _MODEL
    ):
        return None
    context = SEP.join(documents)
    added = state.get("added_tokens")
    if not isinstance(added, list):
        return None
    for token in added:
        content = token.get("content")
        if (
            not isinstance(content, str)
            or not content
            or not content.isascii()
            or any(
                token.get(flag) is not False
                for flag in ("normalized", "lstrip", "rstrip", "single_word")
            )
            or content in context
        ):
            return None
    prefix = [0]
    final_counts = []
    concatenated_ids: list[int] = []
    with sanitized_attestation_environment():
        for index, document in enumerate(documents):
            ids = tokenizer.encode(document, add_special_tokens=False)
            final_counts.append(len(ids))
            if index + 1 < len(documents):
                ids = tokenizer.encode(document + SEP, add_special_tokens=False)
                prefix.append(prefix[-1] + len(ids))
            concatenated_ids.extend(ids)
        whole_ids = tokenizer.encode(context, add_special_tokens=False)
        if concatenated_ids != whole_ids or token_counter(context) != len(whole_ids):
            return None
    result = JsonWindowTokenCounter(
        tuple(prefix),
        tuple(final_counts),
        token_counter,
        tokenizer,
        backend,
        backend_state,
    )
    result.verify_unchanged()
    return result
