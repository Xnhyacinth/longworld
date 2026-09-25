"""Bounded cross-file paper-source reference and caption resolver."""

from __future__ import annotations

import re
from collections import defaultdict

FILE = re.compile(
    r"<<< PAPER_SOURCE_FILE path=([^>\n]+) >>>\n(.*?)\n<<< END_PAPER_SOURCE_FILE >>>\n",
    re.DOTALL,
)
BLOCK = re.compile(r"\\begin\{(figure\*?|table\*?)\}(.*?)\\end\{\1\}", re.DOTALL)
CAPTION = re.compile(r"\\caption(?:\[[^\]\n]{0,120}\])?\{([^{}]{30,450})\}")
LABEL = re.compile(r"\\label\{([^}\n]+)\}")
REF = re.compile(r"\\(?:ref|autoref)\{([^}\n]+)\}")
SECTION = re.compile(
    r"\\(?:section|subsection|subsubsection)\*?\{([^{}\n]{4,150})\}"
    r"[ \t\r\n~]*\\label\{([^}\n]+)\}"
)
WORD = re.compile(r"[A-Za-z]{3,}")
RESULT_PATH = re.compile(
    r"result|experiment|evaluation|analysis|appendix", re.IGNORECASE
)


def _active(text: str, position: int) -> bool:
    """Reject TeX commands after an unescaped comment marker on their line."""
    line = text[text.rfind("\n", 0, position) + 1 : position]
    for index, char in enumerate(line):
        if char == "%":
            slash_count = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                slash_count += 1
                cursor -= 1
            if slash_count % 2 == 0:
                return False
    return True


def render_files(files: dict[str, str]) -> str:
    if not files or any("\n" in path or ">" in path for path in files):
        raise ValueError("invalid paper source paths")
    return "".join(
        f"<<< PAPER_SOURCE_FILE path={path} >>>\n{text.rstrip()}\n"
        "<<< END_PAPER_SOURCE_FILE >>>\n"
        for path, text in sorted(files.items())
    )


def _records(context: str) -> dict[str, tuple[str, int]]:
    records = {}
    matches = list(FILE.finditer(context))
    if not matches or "".join(match.group() for match in matches) != context:
        raise ValueError("paper source record boundaries changed")
    for match in matches:
        path = match.group(1)
        if path in records:
            raise ValueError("paper source file repeated")
        records[path] = match.group(2), match.start(2)
    return records


def _targets(records: dict[str, tuple[str, int]]) -> dict[str, list[dict]]:
    targets = defaultdict(list)
    for path, (text, offset) in records.items():
        for heading in SECTION.finditer(text):
            if not _active(text, heading.start()):
                continue
            title = heading.group(1).strip()
            if not WORD.search(title):
                continue
            targets[heading.group(2)].append(
                {
                    "path": path,
                    "kind": "section",
                    "caption": title,
                    "caption_span": [
                        offset + heading.start(1),
                        offset + heading.end(1),
                    ],
                    "label": heading.group(2),
                }
            )
        for block in BLOCK.finditer(text):
            if not _active(text, block.start()):
                continue
            body = block.group(2)
            captions = [
                item
                for item in CAPTION.finditer(body)
                if _active(text, block.start(2) + item.start())
            ]
            labels = [
                item
                for item in LABEL.finditer(body)
                if _active(text, block.start(2) + item.start())
            ]
            if len(captions) != 1 or len(labels) != 1:
                continue
            caption = captions[0].group(1)
            if len(WORD.findall(caption)) < 5 or "\\input" in caption:
                continue
            start = offset + block.start(2) + captions[0].start(1)
            targets[labels[0].group(1)].append(
                {
                    "path": path,
                    "kind": "figure"
                    if block.group(1).startswith("figure")
                    else "table",
                    "caption": " ".join(caption.split()),
                    "caption_span": [start, start + len(caption)],
                    "label": labels[0].group(1),
                }
            )
    return targets


def cue_for_reference(text: str, reference: re.Match[str]) -> str | None:
    """Choose one exact, non-label prose fragment adjacent to a sole reference."""
    line_start = text.rfind("\n", 0, reference.start()) + 1
    line_end = text.find("\n", reference.end())
    line_end = len(text) if line_end < 0 else line_end
    line = text[line_start:line_end]
    if len(line) > 3000:
        return None
    before = text[line_start : reference.start()].strip()
    after = text[reference.end() : line_end].strip()
    left = before[-135:]
    if len(before) > 135 and " " in left:
        left = left.split(" ", 1)[1]
    right = after[:135]
    if len(after) > 135 and " " in right:
        right = right.rsplit(" ", 1)[0]
    candidates = (left, right)
    for raw in candidates:
        cue = raw.strip(" \t.,;:()[]")
        if (
            len(cue) > 25
            and len(WORD.findall(cue)) >= 6
            and not re.search(r"\\[A-Za-z]+|[{}]", cue)
        ):
            return cue
    return None


def discover(context: str) -> tuple[list[dict], dict[str, int]]:
    """Find cross-file result/experiment references to unique captions."""
    records = _records(context)
    targets = _targets(records)
    reasons: dict[str, int] = defaultdict(int)
    found = []
    for path, (text, offset) in records.items():
        if not RESULT_PATH.search(path):
            continue
        for ref in REF.finditer(text):
            reasons["refs_examined"] += 1
            if not _active(text, ref.start()):
                reasons["commented_ref"] += 1
                continue
            options = targets.get(ref.group(1), [])
            if len(options) != 1 or options[0]["path"] == path:
                reasons["nonunique_or_same_file_target"] += 1
                continue
            cue = cue_for_reference(text, ref)
            if cue is None or context.count(cue) != 1:
                reasons["ambiguous_or_nonprose_cue"] += 1
                continue
            target = options[0]
            found.append(
                {
                    "cue": cue,
                    "source_path": path,
                    "reference_label": ref.group(1),
                    "reference_span": [offset + ref.start(), offset + ref.end()],
                    "target": target,
                }
            )
    return found, dict(reasons)


def resolve(context: str, cue: str) -> dict | None:
    """Resolve only from the final reader's file records and visible TeX."""
    if not cue or context.count(cue) != 1:
        return None
    records = _records(context)
    targets = _targets(records)
    hits = []
    for path, (text, offset) in records.items():
        at = text.find(cue)
        if at < 0:
            continue
        nearby_start = max(0, at - 180)
        nearby_end = min(len(text), at + len(cue) + 180)
        refs = [
            item
            for item in REF.finditer(text, nearby_start, nearby_end)
            if _active(text, item.start())
        ]
        if len(refs) != 1:
            return None
        ref = refs[0]
        options = targets.get(ref.group(1), [])
        if len(options) != 1 or options[0]["path"] == path:
            return None
        hits.append(
            {
                "caption": options[0]["caption"],
                "kind": options[0]["kind"],
                "target_path": options[0]["path"],
                "reference_label": ref.group(1),
                "reference_span": [offset + ref.start(), offset + ref.end()],
                "caption_span": options[0]["caption_span"],
            }
        )
    return hits[0] if len(hits) == 1 else None


def shortcut_reason(cue: str, label: str, answer: str, kind: str) -> str | None:
    """Reject answers printed in the cue or recoverable from a section label."""

    def compact(value: str) -> str:
        return "".join(char.lower() for char in value if char.isalnum())

    gold = compact(answer)
    if not gold:
        return "empty_answer"
    if gold in compact(cue):
        return "answer_in_question_cue"
    tail = compact(label.rsplit(":", 1)[-1])
    if len(tail) >= 3 and gold in tail:
        return "answer_encoded_in_label"
    if (
        kind == "section"
        and len(answer.split()) <= 2
        and len(tail) >= 4
        and tail in gold
    ):
        return "short_heading_label_keyword"
    if (
        kind == "section"
        and len(answer.split()) == 1
        and len(tail) >= 3
        and gold.startswith(tail)
    ):
        return "one_word_heading_label_prefix"
    return None
