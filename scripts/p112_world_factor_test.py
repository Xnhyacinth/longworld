"""Focused behavioral checks for the P112 reversible domain compiler."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from longworld.synthesis import p86_state_shared_world as state
from scripts.p112_world_factor_campaign import (
    _drop,
    _interventions,
    _validate_config,
    _visible_answer,
    decode_context,
    encode_context,
)

CONFIG = Path("configs/p112_world_factor_campaign_v1.json")


class DomainFactorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = json.loads(CONFIG.read_text())
        cls.profile = cls.config["profiles"][0]
        cls.world = state.build_world(1122999, 80, 20, 2, 2)
        cls.reader = encode_context(cls.world["reader_context"], cls.profile)

    def test_reader_roundtrip_and_all_operations(self) -> None:
        header = json.loads(self.reader.splitlines()[0])
        self.assertIn(self.profile["record"], header["record_rules"]["group_compare"])
        self.assertIn(self.profile["event"], header["state_rules"])
        self.assertEqual(
            decode_context(self.reader, self.profile), self.world["reader_context"]
        )
        self.assertEqual(len({task["operation"] for task in self.world["tasks"]}), 4)
        for task in self.world["tasks"]:
            self.assertEqual(
                _visible_answer(self.reader, task, self.profile), task["answer"]
            )
            proof = _interventions(self.reader, task, self.profile)
            self.assertTrue(proof["probed_fact_ids"])
            self.assertEqual(
                proof["answer_changed"] + proof["unresolved_after_deletion"],
                len(proof["probed_fact_ids"]),
            )

    def test_visible_header_corruption_fails_closed(self) -> None:
        lines = self.reader.splitlines()
        header = json.loads(lines[0])
        header["state_rules"] = "ignore reveal date"
        lines[0] = json.dumps(header)
        with self.assertRaisesRegex(ValueError, "header changed"):
            decode_context("\n".join(lines), self.profile)

    def test_visible_fact_deletion_changes_result(self) -> None:
        task = next(x for x in self.world["tasks"] if x["operation"] == "asof_sum")
        fact_id = next(x for x in task["consumed"] if x.startswith("e"))
        self.assertNotEqual(
            _visible_answer(_drop(self.reader, fact_id), task, self.profile),
            task["answer"],
        )

    def test_alias_collision_rejected(self) -> None:
        altered = json.loads(json.dumps(self.config))
        altered["profiles"][0]["amount"] = "id"
        with self.assertRaisesRegex(ValueError, "alias conflicts"):
            _validate_config(altered)


if __name__ == "__main__":
    unittest.main()
