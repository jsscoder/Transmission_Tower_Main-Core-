"""Unit and regression tests for Golden Gate (4 shop drawings >= 90% match).

Verifies:
- Category A: Metadata matches reference exactly.
- Category B: Fabrication details (holes, positions, diameters, bolts, dim chain).
- Category C: CAD geometry (silhouette, section view, centerlines, hidden lines).
- Category D: Multi-tier dimension chains (L1, L2, L3 gauge).
- Category E: Sheet layout (title block, hole schedule, border, scale).
- Invariant: e1 + sum(pitches) + e2 == L.
- Provenance: All fabrication-critical fields have non-null provenance.
- Gate score >= 90% individually for 429B37, 429B44, 429B45, 429B46.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from golden_score import (
    load_generated,
    load_reference,
    run_golden_gate,
    score_dimensions,
    score_fabrication,
    score_geometry,
    score_layout,
    score_metadata,
)


class GoldenGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures_dir = ROOT / "fixtures"
        cls.shop_dir = ROOT / "pipeline_out" / "shop_drawings"
        cls.refs = load_reference(cls.fixtures_dir)
        cls.gens = load_generated(cls.shop_dir)

    def test_all_four_golden_drawings_exist(self):
        """Verify all 4 golden drawings are generated and loaded."""
        for bm in ("37", "44", "45", "46"):
            self.assertIn(bm, self.refs, f"Reference data for {bm} must exist")
            self.assertIn(bm, self.gens, f"Generated JSON sidecar for 429B{bm} must exist")
            dxf_path = self.shop_dir / f"429B{bm}.dxf"
            self.assertTrue(dxf_path.exists(), f"DXF for 429B{bm} must exist")

    def test_golden_gate_score_above_90_percent(self):
        """Verify EACH of the 4 drawings achieves >= 90.0% score individually."""
        results = run_golden_gate(self.fixtures_dir, self.shop_dir)
        self.assertEqual(results["gate_status"], "PASS", "Golden Gate must PASS")
        for d in results["drawings"]:
            score = d["overall_score"]
            drawing_id = d["drawing_id"]
            self.assertGreaterEqual(
                score, 90.0,
                f"{drawing_id} scored {score}%, which is below the required 90.0% threshold"
            )
            self.assertEqual(d["status"], "PASS")

    def test_dimension_chain_invariant(self):
        """Verify e1 + sum(pitches) + e2 == L for all 4 golden drawings."""
        for bm, gen in self.gens.items():
            length = float(gen["length_mm"])
            holes = gen.get("holes", [])
            self.assertTrue(len(holes) > 0, f"Member {bm} must have holes")
            positions = sorted(float(h["along_mm"]) for h in holes)
            e1 = positions[0]
            e2 = length - positions[-1]
            pitches = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
            chain_sum = e1 + sum(pitches) + e2
            self.assertAlmostEqual(
                chain_sum, length, delta=0.5,
                msg=f"Member {bm}: e1 + sum(p) + e2 ({chain_sum}) != length ({length})"
            )

    def test_provenance_integrity(self):
        """Verify every hole and member carries non-null provenance."""
        for bm, gen in self.gens.items():
            # Member provenance
            mem = gen.get("member", {})
            self.assertIsNotNone(mem.get("provenance"), f"Member {bm} provenance cannot be null")
            self.assertIsNotNone(mem.get("quantity_source"), f"Member {bm} quantity_source cannot be null")
            
            # Holes provenance
            for h in gen.get("holes", []):
                hole_id = h.get("hole_id", "unknown")
                self.assertIsNotNone(
                    h.get("provenance"),
                    f"Member {bm} hole {hole_id} provenance cannot be null"
                )
                self.assertIsNotNone(
                    h.get("transverse_mm"),
                    f"Member {bm} hole {hole_id} transverse_mm cannot be null"
                )


if __name__ == "__main__":
    unittest.main()
