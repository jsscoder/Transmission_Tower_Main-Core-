"""Unit tests for structured review queue (Phase 12).

Tests build_review_queue() and write_review_queue() with:
- Missing fabrication holes (BLOCKING)
- Missing quantity (BLOCKING)
- Ambiguous connections & shared joints (WARNING)
- Resolver review decisions (WARNING)
- JSON and CSV serialization
"""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from review_queue import build_review_queue, write_review_queue


class ReviewQueueTests(unittest.TestCase):
    def test_review_queue_from_inventory_blocked_members(self):
        """Verify blocked inventory items generate structured review items with BLOCKING severity."""
        mock_inventory = {
            "total_unique_shop_drawings_identified": 3,
            "currently_buildable_with_design_input": 1,
            "buildable_backmarks": ["37"],
            "blocked": [
                {
                    "backmark": "21",
                    "reason": "MISSING_FABRICATION_HOLES",
                    "reasons": ["MISSING_FABRICATION_HOLES"],
                },
                {
                    "backmark": "30",
                    "reason": "MISSING_QTY, MISSING_FABRICATION_HOLES",
                    "reasons": ["MISSING_QTY", "MISSING_FABRICATION_HOLES"],
                },
            ],
        }
        mock_schedule = {
            "21": {"section": "L90x90x6", "length_mm": 1849},
            "30": {"section": "4 thk x94", "length_mm": 234},
            "37": {"section": "L50x50x5", "length_mm": 2294},
        }

        queue = build_review_queue(inventory=mock_inventory, schedule=mock_schedule)
        summary = queue["summary"]
        self.assertEqual(summary["total_items"], 3)
        self.assertEqual(summary["blocking_count"], 3)
        self.assertEqual(summary["warning_count"], 0)
        self.assertEqual(summary["by_reason"]["MISSING_FABRICATION_HOLES"], 2)
        self.assertEqual(summary["by_reason"]["MISSING_QTY"], 1)

        items = queue["items"]
        item_21 = [i for i in items if i["backmark"] == "21"][0]
        self.assertEqual(item_21["severity"], "BLOCKING")
        self.assertEqual(item_21["reason"], "MISSING_FABRICATION_HOLES")
        self.assertIn("L90x90x6", item_21["reviewer_context"])
        self.assertTrue(item_21["actionable_recommendation"])

    def test_review_queue_from_resolver_ambiguities_and_reviews(self):
        """Verify connection resolver decisions generate review items with WARNING severity."""
        mock_resolved = {
            "decisions": [
                {
                    "backmark": "44",
                    "end_id": "E1",
                    "joint_id": "J10",
                    "group_id": "GRP_1",
                    "status": "REVIEW",
                    "score": 0.65,
                    "competing_owner": True,
                    "raw_callouts": ["4-M12"],
                },
                {
                    "backmark": "45",
                    "end_id": "E2",
                    "joint_id": "J12",
                    "group_id": "GRP_2",
                    "status": "REVIEW",
                    "score": 0.55,
                    "competing_owner": False,
                    "raw_callouts": ["2-M12"],
                },
                {
                    "backmark": "46",
                    "end_id": "E1",
                    "joint_id": "J15",
                    "group_id": "GRP_3",
                    "status": "REJECT",
                    "score": 0.20,
                    "competing_owner": False,
                    "raw_callouts": [],
                },
            ]
        }

        queue = build_review_queue(resolved=mock_resolved)
        summary = queue["summary"]
        self.assertEqual(summary["total_items"], 3)
        self.assertEqual(summary["blocking_count"], 0)
        self.assertEqual(summary["warning_count"], 3)

        items = queue["items"]
        competing_item = [i for i in items if i["backmark"] == "44"][0]
        self.assertEqual(competing_item["reason"], "AMBIGUOUS_CONNECTION")
        self.assertEqual(competing_item["severity"], "WARNING")

        review_item = [i for i in items if i["backmark"] == "45"][0]
        self.assertEqual(review_item["reason"], "REVIEW_REQUIRED")
        self.assertEqual(review_item["severity"], "WARNING")

    def test_review_queue_file_export_json_and_csv(self):
        """Verify write_review_queue outputs review_queue.json and review_queue.csv correctly."""
        mock_inventory = {
            "blocked": [
                {"backmark": "21", "reasons": ["MISSING_FABRICATION_HOLES"]},
            ]
        }

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            queue = build_review_queue(inventory=mock_inventory, out_dir=out_dir)

            json_file = out_dir / "review_queue.json"
            csv_file = out_dir / "review_queue.csv"

            self.assertTrue(json_file.exists())
            self.assertTrue(csv_file.exists())

            # Check JSON
            data = json.loads(json_file.read_text(encoding="utf-8"))
            self.assertEqual(data["summary"]["total_items"], 1)
            self.assertEqual(data["items"][0]["backmark"], "21")

            # Check CSV
            with open(csv_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["backmark"], "21")
                self.assertEqual(row["severity"], "BLOCKING")
                self.assertEqual(row["reason"], "MISSING_FABRICATION_HOLES")
                self.assertIn("actionable_recommendation", row)

    def test_review_queue_handles_string_reasons_and_none_scores(self):
        """Review queue must parse string reasons and handle None/non-numeric decision scores without error."""
        mock_inventory = {
            "blocked": [
                {
                    "backmark": "30",
                    "reasons": "MISSING_QTY, MISSING_FABRICATION_HOLES",
                }
            ]
        }
        mock_resolved = {
            "decisions": [
                {
                    "backmark": "21",
                    "status": "REVIEW",
                    "score": None,  # None score should not raise TypeError
                    "competing_owner": False,
                }
            ]
        }
        queue = build_review_queue(inventory=mock_inventory, resolved=mock_resolved)
        self.assertEqual(queue["summary"]["total_items"], 3)
        self.assertEqual(queue["summary"]["blocking_count"], 2)
        self.assertEqual(queue["summary"]["warning_count"], 1)

    def test_review_queue_empty_inputs(self):
        """Review queue with empty or None inputs returns empty items structure safely."""
        queue = build_review_queue()
        self.assertEqual(queue["summary"]["total_items"], 0)
        self.assertEqual(len(queue["items"]), 0)


if __name__ == "__main__":
    unittest.main()

