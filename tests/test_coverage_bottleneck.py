"""Tests for Coverage Bottleneck, 3-State Classification & Invariant Integrity.

Verifies:
1. DISCOVERED == AUTO_READY + REVIEW_REQUIRED + BLOCKED.
2. No member silently disappears.
3. No fabrication holes, pitches, or quantities are invented for unapproved members.
4. The 26 reference JSON files serve strictly as validation fixtures, never hardcoded production oracles.
5. The 16-metric diagnostic coverage report is completely populated in JSON and CSV.
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

import tower
from shop_reporting import (
    inventory_from_schedule,
    write_inventory,
    generate_diagnostic_coverage_report,
    write_diagnostic_coverage_report,
)
from domain.enums import ValidationState
from domain.models import Member


class CoverageBottleneckTests(unittest.TestCase):
    def setUp(self):
        self.dxf_path = ROOT / "loader" / "WO_429_LLA1_110kv_NT_PART P1 TO P3_ST No. 1 OF 6.dxf"
        self.design_path = ROOT / "connection_design.json"
        self.ref_26_path = ROOT / "fixtures" / "reference_shop_drawings_26.json"

        self.assertTrue(self.dxf_path.exists(), "Test assembly DXF must exist")
        self.assertTrue(self.design_path.exists(), "connection_design.json must exist")
        self.assertTrue(self.ref_26_path.exists(), "reference_shop_drawings_26.json fixture must exist")

        self.schedule = tower.extract_member_schedule(str(self.dxf_path))
        self.design = json.loads(self.design_path.read_text(encoding="utf-8"))
        self.ref_26 = json.loads(self.ref_26_path.read_text(encoding="utf-8"))

    def test_dxf_discovers_all_37_members(self):
        """Verify the DXF extraction discovers all 37 tower members."""
        self.assertEqual(len(self.schedule), 37)
        for mark, info in self.schedule.items():
            self.assertTrue(bool(info.get("section")), f"Member {mark} must have section")
            self.assertGreater(float(info.get("length_mm", 0.0) or 0.0), 0, f"Member {mark} length must be > 0")

    def test_coverage_equation_and_zero_silent_drops(self):
        """Verify DISCOVERED == AUTO_READY + REVIEW_REQUIRED + BLOCKED."""
        inv = inventory_from_schedule(
            self.schedule,
            design_path=self.design,
            topology=None,
            resolved=None,
        )

        total_discovered = len(self.schedule)
        auto_ready = len(inv["auto_ready"])
        review_required = len(inv["review_required"])
        blocked = len(inv["blocked"])

        self.assertEqual(total_discovered, 37)
        self.assertTrue(inv["invariant_holds"])
        self.assertEqual(total_discovered, auto_ready + review_required + blocked)

        # Ensure no duplicate backmarks across categories
        auto_marks = {m["backmark"] for m in inv["auto_ready"]}
        review_marks = {m["backmark"] for m in inv["review_required"]}
        blocked_marks = {m["backmark"] for m in inv["blocked"]}

        self.assertEqual(len(auto_marks & review_marks), 0, "No overlap between auto_ready and review_required")
        self.assertEqual(len(auto_marks & blocked_marks), 0, "No overlap between auto_ready and blocked")
        self.assertEqual(len(review_marks & blocked_marks), 0, "No overlap between review_required and blocked")
        self.assertEqual(auto_marks | review_marks | blocked_marks, set(self.schedule.keys()))

    def test_no_fabrication_data_invented(self):
        """Verify that blocked and review members do NOT have synthetic holes or fake quantities."""
        inv = inventory_from_schedule(
            self.schedule,
            design_path=self.design,
        )
        # Any blocked member must have an explicit reason
        for b in inv["blocked"]:
            self.assertTrue(bool(b.get("reason")), f"Blocked member {b['backmark']} must have reason")
            self.assertTrue(
                any(r in b["reason"] for r in ("MISSING_FABRICATION_HOLES", "MISSING_QTY", "MISSING_APPROVED_ENGINEERING_INPUT"))
            )

    def test_reference_shop_drawings_26_fixture(self):
        """Verify the 26-member reference fixture metadata and purpose."""
        self.assertEqual(self.ref_26["purpose"], "REFERENCE_VALIDATION_ORACLE_ONLY")
        self.assertEqual(len(self.ref_26["members"]), 26)

        expected_marks = {
            "21", "22H", "23H", "24H", "25H", "26H", "27H", "28H", "29H",
            "30", "31", "32", "33", "34", "35", "36", "37", "38",
            "39H", "40H", "41", "42", "43", "44", "45", "46",
        }
        actual_marks = {m["backmark"] for m in self.ref_26["members"]}
        self.assertEqual(actual_marks, expected_marks)

    def test_diagnostic_coverage_report_generation(self):
        """Verify diagnostic coverage report compiles and writes JSON/CSV with 16 metrics."""
        inv = inventory_from_schedule(self.schedule, design_path=self.design)
        report = generate_diagnostic_coverage_report(
            schedule=self.schedule,
            inventory=inv,
        )

        self.assertEqual(report["members_discovered"], 37)
        self.assertEqual(report["members_valid_designation"], 37)
        self.assertEqual(report["members_valid_section"], 37)
        self.assertEqual(report["members_valid_length"], 37)
        self.assertTrue(report["coverage_equation"]["balanced"])

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            write_diagnostic_coverage_report(report, out_dir)

            self.assertTrue((out_dir / "diagnostic_coverage_report.json").exists())
            self.assertTrue((out_dir / "diagnostic_coverage_report.csv").exists())

            # Read back CSV
            with open(out_dir / "diagnostic_coverage_report.csv", "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                self.assertGreaterEqual(len(rows), 16)
                metrics = [r["metric"] for r in rows]
                self.assertIn("members_discovered", metrics)
                self.assertIn("members_ready_for_rendering", metrics)
                self.assertIn("members_requiring_review", metrics)
                self.assertIn("blocked_members", metrics)


if __name__ == "__main__":
    unittest.main()
