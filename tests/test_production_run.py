"""End-to-end integration and production run tests (Phase 13).

Verifies the orchestrator produces the complete suite of final reports in pipeline_out/:
- run_summary.json / manifest.json: summary statistics (members_total: 37, buildable, generated, passed, review, blocked, reference_drawings: 26, validated_reference: 26, not_validated: 11).
- drawing_inventory.csv / drawing_inventory.json: 37 members categorized as BUILDABLE or BLOCKED with explicit reasons.
- job_bom.csv & job_bom.json: derived strictly from canonical models.
- review_queue.json & review_queue.csv.
- regression_report.json & regression_report.csv: scorecard for the 26 reference drawings against Gates A through H, and the 11 non-reference members explicitly labeled NOT_VALIDATED.
- shop drawings in pipeline_out/shop_drawings/ (.dxf, .pdf, .json).
"""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from app_logger import close_logging
from inference import run_pipeline


class ProductionRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dxf_path = ROOT / "loader" / "WO_429_LLA1_110kv_NT_PART P1 TO P3_ST No. 1 OF 6.dxf"
        cls.design_path = ROOT / "connection_design.json"
        cls.rules_path = ROOT / "design_rules.json"

    def tearDown(self):
        close_logging()

    def test_production_run_artifacts_and_reports_end_to_end(self):
        """Execute production pipeline on full assembly DXF and verify all required report suites."""
        if not self.dxf_path.exists():
            self.skipTest(f"Assembly DXF not found at {self.dxf_path}")

        out_dir = ROOT / "pipeline_out"
        # Remove old shop_drawings so only the new buildable members are present
        shop_dir = out_dir / "shop_drawings"
        if shop_dir.exists():
            import shutil
            shutil.rmtree(shop_dir, ignore_errors=True)

        try:
            summary = run_pipeline(
                str(self.dxf_path),
                out_dir=str(out_dir),
                engineering_input=str(self.design_path),
                rules=str(self.rules_path),
                generate=True,
                dpi=150,  # Fast DPI for test
            )

            # 1. Verify run_summary.json & manifest.json
            run_sum_file = out_dir / "run_summary.json"
            manifest_file = out_dir / "manifest.json"
            self.assertTrue(run_sum_file.exists(), "run_summary.json must exist")
            self.assertTrue(manifest_file.exists(), "manifest.json must exist")

            run_sum = json.loads(run_sum_file.read_text(encoding="utf-8"))
            self.assertEqual(run_sum["members_total"], 37)
            self.assertEqual(run_sum["reference_drawings"], 26)
            self.assertEqual(run_sum["validated_reference"], 26)
            self.assertEqual(run_sum["not_validated"], 11)
            self.assertEqual(run_sum["buildable"], 4)
            self.assertEqual(run_sum["generated"], 4)
            self.assertEqual(run_sum["blocked"], 33)
            self.assertTrue(run_sum["overall_gates_passed"])

            # 2. Verify drawing_inventory.csv & drawing_inventory.json
            inv_json = out_dir / "drawing_inventory.json"
            inv_csv = out_dir / "drawing_inventory.csv"
            self.assertTrue(inv_json.exists(), "drawing_inventory.json must exist")
            self.assertTrue(inv_csv.exists(), "drawing_inventory.csv must exist")

            with open(inv_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                self.assertEqual(len(rows), 37)
                buildable_rows = [r for r in rows if r["status"] == "BUILDABLE"]
                blocked_rows = [r for r in rows if r["status"] == "BLOCKED"]
                self.assertEqual(len(buildable_rows), 4)
                self.assertEqual(len(blocked_rows), 33)
                for br in blocked_rows:
                    self.assertIn("MISSING_FABRICATION_HOLES", br["reason"])

            # 3. Verify job_bom.csv & job_bom.json
            bom_json = out_dir / "job_bom.json"
            bom_csv = out_dir / "job_bom.csv"
            self.assertTrue(bom_json.exists(), "job_bom.json must exist")
            self.assertTrue(bom_csv.exists(), "job_bom.csv must exist")
            bom_data = json.loads(bom_json.read_text(encoding="utf-8"))
            self.assertIsInstance(bom_data, list)
            self.assertGreater(len(bom_data), 0)
            self.assertIn("backmark", bom_data[0])

            # 4. Verify review_queue.json & review_queue.csv
            rq_json = out_dir / "review_queue.json"
            rq_csv = out_dir / "review_queue.csv"
            self.assertTrue(rq_json.exists(), "review_queue.json must exist")
            self.assertTrue(rq_csv.exists(), "review_queue.csv must exist")
            rq_data = json.loads(rq_json.read_text(encoding="utf-8"))
            self.assertGreater(rq_data["summary"]["total_items"], 0)
            self.assertEqual(rq_data["summary"]["blocking_count"], 33)
            self.assertIn("MISSING_FABRICATION_HOLES", rq_data["summary"]["by_reason"])

            # 5. Verify regression_report.json & regression_report.csv
            reg_json = out_dir / "regression_report.json"
            reg_csv = out_dir / "regression_report.csv"
            self.assertTrue(reg_json.exists(), "regression_report.json must exist")
            self.assertTrue(reg_csv.exists(), "regression_report.csv must exist")

            with open(reg_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                scorecard_rows = list(reader)
                self.assertEqual(len(scorecard_rows), 37)

                ref_scorecard = [r for r in scorecard_rows if r["is_reference"] == "True"]
                non_ref_scorecard = [r for r in scorecard_rows if r["is_reference"] == "False"]
                self.assertEqual(len(ref_scorecard), 26)
                self.assertEqual(len(non_ref_scorecard), 11)

                # Gate H: Non-reference members labeled NOT_VALIDATED
                for nr in non_ref_scorecard:
                    self.assertEqual(nr["validation_status"], "NOT_VALIDATED")
                    self.assertEqual(nr["gate_h_no_false_positives"], "PASS")

                # Diagnostic cases: 37, 44, 45, 46 have full VALIDATED_REFERENCE
                for diag in [r for r in ref_scorecard if r["backmark"] in ("37", "44", "45", "46")]:
                    self.assertEqual(diag["validation_status"], "VALIDATED_REFERENCE")
                    self.assertEqual(diag["gate_d_holes"], "PASS")
                    self.assertEqual(diag["gate_e_dimensions"], "PASS")

            # 6. Verify shop drawings (.dxf, .pdf, .json) for buildable members
            shop_dir = out_dir / "shop_drawings"
            self.assertTrue(shop_dir.exists())
            for mark in ["37", "44", "45", "46"]:
                self.assertTrue((shop_dir / f"429B{mark}.dxf").exists())
                self.assertTrue((shop_dir / f"429B{mark}.pdf").exists())
                self.assertTrue((shop_dir / f"429B{mark}.json").exists())

            # Ensure blocked members were not generated
            for blocked_mark in ["21", "22H", "30", "49H", "373H"]:
                self.assertFalse((shop_dir / f"429B{blocked_mark}.dxf").exists())

        finally:
            close_logging()


if __name__ == "__main__":
    unittest.main()
