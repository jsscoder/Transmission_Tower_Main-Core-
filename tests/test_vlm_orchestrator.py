"""Unit tests for ConsensusGate and VLM Orchestrator."""
import unittest
from pathlib import Path
from consensus_gate import (
    compute_standard_hole_positions,
    snap_to_cad_circles,
    validate_is802_compliance,
    synthesize_canonical_design_entry,
)
from vlm_orchestrator import synthesize_design_input


class TestConsensusGateAndVLMOrchestrator(unittest.TestCase):
    def test_compute_standard_hole_positions_e1(self):
        # 1849mm member, 2 M16 bolts at E1
        pos = compute_standard_hole_positions(1849.0, 2, nominal_bolt_dia_mm=16, end="E1")
        self.assertEqual(len(pos), 2)
        self.assertEqual(pos[0], 25.0)  # Standard edge distance for M16
        self.assertEqual(pos[1], 70.0)  # 25 + 45 pitch

    def test_compute_standard_hole_positions_e2(self):
        # 1849mm member, 2 M16 bolts at E2
        pos = compute_standard_hole_positions(1849.0, 2, nominal_bolt_dia_mm=16, end="E2")
        self.assertEqual(len(pos), 2)
        self.assertEqual(pos[1], 1849.0 - 25.0)  # 1824.0
        self.assertEqual(pos[0], 1849.0 - 70.0)  # 1779.0

    def test_snap_to_cad_circles(self):
        calculated = [25.0, 70.0]
        cad_circles = [24.8, 70.2, 500.0]
        snapped = snap_to_cad_circles(calculated, cad_circles, tolerance_mm=1.0)
        self.assertEqual(snapped, [24.8, 70.2])

    def test_validate_is802_compliance(self):
        # Compliant positions
        res = validate_is802_compliance([25.0, 70.0], 1849.0, nominal_bolt_dia_mm=16)
        self.assertTrue(res["valid"])
        self.assertEqual(len(res["violations"]), 0)

        # Non-compliant edge distance (< 1.5 * 16 = 24mm)
        res_bad = validate_is802_compliance([15.0, 70.0], 1849.0, nominal_bolt_dia_mm=16)
        self.assertFalse(res_bad["valid"])
        self.assertIn("edge distance", res_bad["violations"][0])

    def test_synthesize_canonical_design_entry(self):
        end_props = {
            "E1": {"bolt_quantity": 2, "nominal_bolt_diameter_mm": 16, "confidence": 0.95},
            "E2": {"bolt_quantity": 2, "nominal_bolt_diameter_mm": 16, "confidence": 0.95},
        }
        entry = synthesize_canonical_design_entry("113H", "HTL100x100x8", 6115.0, 2, end_props)
        self.assertEqual(entry["backmark"], "113H")
        self.assertEqual(entry["section"], "HTL100x100x8")
        self.assertEqual(entry["length_mm"], 6115.0)
        self.assertEqual(entry["quantity"], 2)
        self.assertIn("E1", entry["connection_ends"])
        self.assertIn("E2", entry["connection_ends"])
        self.assertEqual(len(entry["connection_ends"]["E1"]["holes"]), 2)
        self.assertEqual(len(entry["connection_ends"]["E2"]["holes"]), 2)

    def test_synthesize_design_input(self):
        schedule = {
            "113H": {"section": "HTL100x100x8", "length_mm": "6115", "count": 2},
            "119": {"section": "L55x55x5", "length_mm": "7716", "count": 2},
        }
        topology = {"joints": []}
        resolved = {"members": {}}
        res = synthesize_design_input(schedule, topology, resolved)
        self.assertIn("113H", res)
        self.assertIn("119", res)
        self.assertEqual(res["113H"]["quantity"], 2)
        self.assertEqual(res["119"]["quantity"], 2)


if __name__ == "__main__":
    unittest.main()
