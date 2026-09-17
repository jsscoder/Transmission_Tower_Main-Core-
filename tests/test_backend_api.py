"""Automated tests for FastAPI backend service and real engine integration."""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from backend.main import app
from backend.jobs import job_manager


class BackendApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.dxf_path = ROOT / "loader" / "WO_429_LLA1_110kv_NT_PART P1 TO P3_ST No. 1 OF 6.dxf"
        cls.design_path = ROOT / "connection_design.json"

    def test_health_endpoint(self):
        """Verify health check returns ok."""
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

    def test_list_jobs(self):
        """Verify list jobs returns a list."""
        res = self.client.get("/api/jobs")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json(), list)

    def test_path_traversal_protection(self):
        """Verify artifact endpoint strictly forbids path traversal."""
        # Create a mock job to test path validation
        job = job_manager.create_job(
            name="security_test",
            dxf_filename="test.dxf",
            dxf_bytes=b"dummy",
        )
        res = self.client.get(f"/api/jobs/{job.id}/artifacts/../../../../Windows/win.ini")
        self.assertIn(res.status_code, (400, 403, 404))

    def test_engine_adapter_on_existing_output(self):
        """Verify engine adapter accurately parses real existing pipeline_out."""
        # Create a job pointing to existing pipeline_out
        out_dir = ROOT / "pipeline_out"
        if not out_dir.exists():
            self.skipTest("pipeline_out does not exist")

        job = job_manager.create_job(
            name="adapter_test",
            dxf_filename="test.dxf",
            dxf_bytes=b"dummy",
        )
        # Point output_dir to pipeline_out
        job_manager.update_job(job.id, output_dir=str(out_dir))

        # 1. Test metrics
        res = self.client.get(f"/api/jobs/{job.id}/metrics")
        self.assertEqual(res.status_code, 200)
        metrics = res.json()
        self.assertEqual(metrics["members_total"], 37)
        self.assertGreaterEqual(metrics["buildable_count"], 4)
        self.assertGreaterEqual(metrics["generated_drawings"], 4)
        self.assertEqual(metrics["golden_gate_status"], "PASS")

        # 2. Test members
        res = self.client.get(f"/api/jobs/{job.id}/members")
        self.assertEqual(res.status_code, 200)
        members = res.json()
        self.assertEqual(len(members), 37)

        # 3. Test drawings
        res = self.client.get(f"/api/jobs/{job.id}/drawings")
        self.assertEqual(res.status_code, 200)
        drawings = res.json()
        self.assertGreaterEqual(len(drawings), 4)

        # 4. Test golden regression
        res = self.client.get(f"/api/jobs/{job.id}/regression")
        self.assertEqual(res.status_code, 200)
        scores = res.json()
        self.assertGreaterEqual(len(scores), 4)
        for s in scores:
            self.assertGreaterEqual(s["overall_score"], 90.0)
            self.assertEqual(s["status"], "PASS")

        # 5. Test BOM
        res = self.client.get(f"/api/jobs/{job.id}/bom")
        self.assertEqual(res.status_code, 200)
        bom = res.json()
        self.assertGreater(len(bom), 0)

        # 6. Test locators & assembly artifacts
        res = self.client.get(f"/api/jobs/{job.id}/locators")
        self.assertEqual(res.status_code, 200)
        locators = res.json()
        self.assertGreaterEqual(len(locators), 37)
        member_locs = [l for l in locators if l["source_entity"] == "MEMBER_LOCATOR"]
        self.assertEqual(len(member_locs), 37)
        sample_loc = next((l for l in member_locs if l["member_backmark"] == "21"), None)
        self.assertIsNotNone(sample_loc)
        self.assertGreater(sample_loc["x"], 0)
        self.assertGreater(sample_loc["y"], 0)
        self.assertTrue(sample_loc["has_crop"])

        # Test summary endpoint
        res = self.client.get(f"/api/jobs/{job.id}/locators/summary")
        self.assertEqual(res.status_code, 200)
        summary = res.json()
        self.assertEqual(summary["total_members"], 37)
        self.assertIsNotNone(summary["assembly_image_url"])

        # 7. Test artifact download
        res = self.client.get(f"/api/jobs/{job.id}/artifacts/pipeline_summary.json")
        self.assertEqual(res.status_code, 200)

        # 8. Test scale-context endpoint
        res = self.client.get(f"/api/jobs/{job.id}/scale-context")
        if (out_dir / "scale_context.json").exists():
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("scale_factor", data)
            self.assertIn("width_mm", data)


if __name__ == "__main__":
    unittest.main()
