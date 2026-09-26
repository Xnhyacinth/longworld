"""Behavioral checks for bounded acquisition and source-shape campaign planning."""

import json

import pytest

from longworld.synthesis.wiki_adapter import HttpError
from scripts import p131_wiki_html_campaign as campaign


def test_actual_frozen_pool_plans_once_in_bounded_chunks(tmp_path) -> None:
    config = campaign.ROOT / "configs/p131_wiki_html_campaign_v1.json"
    planned = campaign.plan(config, tmp_path / "campaign")
    assert planned["planned_pages"] == 98
    assert planned["chunks"] == 5
    assert planned["max_pages_per_chunk"] == 20
    assert len({row["source_group"] for row in planned["pages"]}) == 98
    assert sum(planned["planned_domain_counts"].values()) == 98


def test_fetched_page_receipt_binds_oldid_and_html(tmp_path) -> None:
    source = {
        "title": "List of museums",
        "oldid": 42,
        "snapshot": {"path": "data/snapshot.json", "sha256": "a" * 64},
    }

    class Client:
        def get_json(self, params):
            assert params["oldid"] == 42
            return {
                "parse": {
                    "revid": 42,
                    "title": source["title"],
                    "text": "<table>Frozen</table>",
                }
            }

    source_dir = tmp_path / "source"
    assert campaign.acquire(source, source_dir, {}, Client(), 1000) == "fetched"
    html, receipt = campaign.cache_paths(source, source_dir)
    campaign.check_cache(source, html, receipt, 1000)
    assert json.loads(receipt.read_text())["requested_oldid"] == 42
    html.write_text("tampered")
    with pytest.raises(ValueError, match="source pin differs"):
        campaign.check_cache(source, html, receipt, 1000)


def test_persistent_rate_limit_stops_after_bounded_attempts(monkeypatch) -> None:
    class Client:
        def get_json(self, params):
            raise HttpError("MediaWiki 429")

    monkeypatch.setattr(campaign, "WikiHttpFetcher", lambda _: Client())
    monkeypatch.setattr(campaign.time, "sleep", lambda _: None)
    rate = campaign.RateClient(
        {
            "api": "https://example.test/api",
            "requests_per_second": 0.5,
            "max_429_retries": 2,
        }
    )
    with pytest.raises(campaign.RateLimited):
        rate.get_json({"oldid": 42})
    assert rate.attempts == 3
