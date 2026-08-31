from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from longworld.core.githistory import (
    _MAX_TOKENIZER_CHARS,
    DeterministicTokenCountCache,
    _split_exact_source_text,
    extract_first_parent_history,
    git_truncation_quality,
)


class FakeTokenizer:
    def __init__(self) -> None:
        self.max_seen_chars = 0

    def __call__(
        self,
        text: str,
        *,
        add_special_tokens: bool,
        return_offsets_mapping: bool,
    ) -> dict[str, list[tuple[int, int]]]:
        assert add_special_tokens is False
        assert return_offsets_mapping is True
        self.max_seen_chars = max(self.max_seen_chars, len(text))
        offsets: list[tuple[int, int]] = []
        start = 0
        for token in text.split():
            token_start = text.index(token, start)
            token_end = token_start + len(token)
            offsets.append((token_start, token_end))
            start = token_end
        return {"offset_mapping": offsets}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "LongWorld Test")
    _git(repo, "config", "user.email", "test@example.com")
    for index in range(3):
        path = repo / "history.txt"
        previous = path.read_text() if path.exists() else ""
        path.write_text(previous + f"event {index} owner person{index}@example.com\n")
        _git(repo, "add", "history.txt")
        _git(
            repo,
            "commit",
            "-q",
            "-m",
            f"record workflow event {index}",
            "--date",
            f"2026-01-0{index + 1}T00:00:00Z",
        )
    return repo


def test_git_history_prechunks_huge_source_before_exact_offsets() -> None:
    tokenizer = FakeTokenizer()
    text = ("source event with real patch\n" * 20_000) + "tail"

    chunks = _split_exact_source_text(text, tokenizer, max_record_tokens=50)

    assert "".join(chunk for _, _, chunk in chunks) == text
    assert tokenizer.max_seen_chars <= _MAX_TOKENIZER_CHARS
    assert all(end > start for start, end, _ in chunks)


def test_git_history_extracts_real_chronological_parent_chain(tmp_path: Path) -> None:
    result = extract_first_parent_history(
        _repo(tmp_path),
        repository="example/repo",
        tokenizer=FakeTokenizer(),
        max_commits=3,
        max_record_tokens=20,
    )

    assert result.commit_count == 3
    assert result.reject_reasons == {}
    assert len(result.records) >= 3
    assert [record.occurred_at for record in result.records] == sorted(
        record.occurred_at for record in result.records
    )
    assert all("@example.com" not in record.text for record in result.records)
    assert any("[redacted-email]" in record.text for record in result.records)
    seen: set[str] = set()
    for record in result.records:
        if seen:
            assert set(record.links) & seen
        else:
            assert record.links == ()
        seen.add(record.record_id)


def test_git_history_secret_commit_breaks_the_exported_component(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "history.txt").write_text(
        "credential ghp_abcdefghijklmnopqrstuvwxyz123456\n"
    )
    _git(repo, "add", "history.txt")
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "unsafe event",
        "--date",
        "2026-01-04T00:00:00Z",
    )
    (repo / "recovery.txt").write_text("safe recovery event\n")
    _git(repo, "add", "recovery.txt")
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "recovery event",
        "--date",
        "2026-01-05T00:00:00Z",
    )

    result = extract_first_parent_history(
        repo,
        repository="example/repo",
        tokenizer=FakeTokenizer(),
        max_commits=5,
        max_record_tokens=40,
    )

    assert result.commit_count == 5
    assert result.reject_reasons == {"credential_shaped_commit": 1}
    recovery = next(
        record for record in result.records if "recovery event" in record.text
    )
    assert recovery.links == ()
    assert not any("ghp_" in record.text for record in result.records)


def test_git_history_commit_slices_do_not_overlap(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    newest = extract_first_parent_history(
        repo,
        repository="example/repo",
        tokenizer=FakeTokenizer(),
        max_commits=1,
        skip_commits=0,
        max_record_tokens=40,
    )
    older = extract_first_parent_history(
        repo,
        repository="example/repo",
        tokenizer=FakeTokenizer(),
        max_commits=2,
        skip_commits=1,
        max_record_tokens=40,
    )

    newest_shas = {record.attributes["sha"] for record in newest.records}
    older_shas = {record.attributes["sha"] for record in older.records}
    assert newest_shas.isdisjoint(older_shas)


def test_git_history_caps_a_commit_without_breaking_the_parent_chain(
    tmp_path: Path,
) -> None:
    result = extract_first_parent_history(
        _repo(tmp_path),
        repository="example/repo",
        tokenizer=FakeTokenizer(),
        max_commits=3,
        max_record_tokens=5,
        max_chunks_per_commit=1,
    )

    assert len(result.records) == 3
    assert result.reject_reasons["commits_truncated"] == 3
    assert result.reject_reasons["commit_chunks_truncated"] > 3
    assert result.records[0].links == ()
    assert result.records[1].links == (result.records[0].record_id,)
    assert result.records[2].links == (result.records[1].record_id,)
    assert all(record.attributes["commit_was_truncated"] for record in result.records)
    assert all(
        record.attributes["commit_chunk_count_total"]
        > record.attributes["commit_chunk_count_emitted"]
        == 1
        for record in result.records
    )
    assert (
        sum(
            item.total_chunk_count - item.emitted_chunk_count
            for item in result.truncated_commits
        )
        == result.reject_reasons["commit_chunks_truncated"]
    )


def test_git_truncation_quality_is_exact_and_fail_closed() -> None:
    quality = git_truncation_quality(
        5,
        {"commits_truncated": 1, "commit_chunks_truncated": 7},
        maximum_truncated_commit_ratio_ppm=200_000,
    )

    assert quality == {
        "revision": "git-observed-prefix-truncation-v1",
        "observed_commit_count": 5,
        "truncated_commit_count": 1,
        "omitted_chunk_count": 7,
        "truncated_commit_ratio_ppm": 200_000,
        "maximum_truncated_commit_ratio_ppm": 200_000,
        "tier": "within_configured_limit",
    }
    with pytest.raises(ValueError, match="exceeds configured maximum"):
        git_truncation_quality(
            5,
            {"commits_truncated": 2, "commit_chunks_truncated": 7},
            maximum_truncated_commit_ratio_ppm=200_000,
        )


def test_token_count_cache_is_deterministic_and_asset_namespaced() -> None:
    calls: list[str] = []

    def count(text: str) -> int:
        calls.append(text)
        return len(text.split())

    first = DeterministicTokenCountCache("asset-a", count)
    second = DeterministicTokenCountCache("asset-b", count)

    assert first("real history") == 2
    assert first("real history") == 2
    assert first.stats() == {"entries": 1, "hits": 1, "misses": 1}
    assert second("real history") == 2
    assert calls == ["real history", "real history"]


def test_git_history_framing_is_not_ambiguous_with_control_bytes(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    (repo / "control.txt").write_bytes(b"real patch \x1e record \x1f field\n")
    _git(repo, "add", "control.txt")
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "control byte regression",
        "--date",
        "2026-01-04T00:00:00Z",
    )

    result = extract_first_parent_history(
        repo,
        repository="example/repo",
        tokenizer=FakeTokenizer(),
        max_commits=4,
        max_record_tokens=40,
    )

    assert result.commit_count == 4
    assert any("control byte regression" in record.text for record in result.records)
