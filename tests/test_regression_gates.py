"""Regression oracles and Acceptance Gates A through H test suite.

Validates the 26 reference drawings against the master implementation specification:
- Gate A (Identity): 26/26 correct backmarks.
- Gate B (Section/Length): 26/26 correct within approved tolerance (<= 2.0 mm).
- Gate C (Quantity): All 26 reference members have verified quantities matching reference metadata.
- Gate D (Holes): Verified hole count, hole diameter classes, and hole positions for diagnostic cases 37, 44, 45, 46.
- Gate E (Dimensions): Verified dimension chains (overall length L, end distances e1, e2, irregular pitch intervals) match ground truth for 37, 44, 45, 46.
- Gate F (Section/End View): Verified section profile families (Angle, Flat, HT_Angle, HT_Flat) render appropriate views.
- Gate H (No False Positives): Never mark a drawing as MATCH merely because metadata matches; non-reference members must be marked NOT_VALIDATED.
"""
from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from shop_reporting import evaluate_regression_gates, write_regression_report, _normalize_section_str
from domain.models import build_dimension_chains, classify_section, SectionType
from shop_drawing import AngleMemberRenderer, FlatMemberRenderer, EndSectionRenderer, _parse_section


class RegressionGatesTests(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = ROOT / "fixtures"
        self.ref_metadata_path = self.fixtures_dir / "reference_metadata_26.json"
        self.assertTrue(self.ref_metadata_path.exists(), "reference_metadata_26.json fixture must exist")
        self.ref_data = json.loads(self.ref_metadata_path.read_text(encoding="utf-8"))

        self.conn_design_path = ROOT / "connection_design.json"
        self.assertTrue(self.conn_design_path.exists(), "connection_design.json must exist")
        self.conn_design = json.loads(self.conn_design_path.read_text(encoding="utf-8"))

    def test_reference_metadata_fixture_structure(self):
        """Verify fixture has exactly 26 reference members and 11 non-reference members."""
        ref_members = self.ref_data["reference_members"]
        non_ref_members = self.ref_data["non_reference_members"]

        self.assertEqual(len(ref_members), 26, "Must contain exactly 26 reference drawings")
        self.assertEqual(len(non_ref_members), 11, "Must contain exactly 11 non-reference members")

        expected_ref_marks = {
            "21", "22H", "23H", "24H", "25H", "26H", "27H", "28H", "29H",
            "30", "31", "32", "33", "34", "35", "36", "37", "38",
            "39H", "40H", "41", "42", "43", "44", "45", "46",
        }
        self.assertEqual(set(ref_members.keys()), expected_ref_marks)

        expected_non_ref = {
            "49H", "50H", "51", "52", "53", "54", "65",
            "373H", "374H", "375H", "376H",
        }
        self.assertEqual(set(non_ref_members), expected_non_ref)

    def test_gate_a_identity_all_26_reference_members(self):
        """Gate A: 26/26 correct backmarks identified."""
        result = evaluate_regression_gates(
            design_input=self.conn_design,
            reference_data=self.ref_data,
        )
        gate_a_stats = result["summary"]["gates"]["gate_a_identity"]
        self.assertEqual(gate_a_stats["pass"], 26)
        self.assertEqual(gate_a_stats["fail"], 0)
        self.assertEqual(gate_a_stats["total"], 26)

        # Negative test: missing backmark fails Gate A
        tampered_design = {"members": [m for m in self.conn_design["members"] if m["backmark"] != "21"]}
        tampered_result = evaluate_regression_gates(
            design_input=tampered_design,
            reference_data=self.ref_data,
        )
        mem21 = [r for r in tampered_result["scorecard"] if r["backmark"] == "21"][0]
        self.assertEqual(mem21["gate_a_identity"], "FAIL")

    def test_gate_b_section_length_within_tolerance(self):
        """Gate B: 26/26 correct section profile and length within tolerance (<= 2.0 mm)."""
        result = evaluate_regression_gates(
            design_input=self.conn_design,
            reference_data=self.ref_data,
            length_tol_mm=2.0,
        )
        gate_b_stats = result["summary"]["gates"]["gate_b_section_length"]
        self.assertEqual(gate_b_stats["pass"], 26)
        self.assertEqual(gate_b_stats["fail"], 0)
        self.assertEqual(gate_b_stats["total"], 26)

        # Negative test: length mismatch > 2.0 mm fails Gate B
        tampered_members = []
        for m in self.conn_design["members"]:
            m_copy = dict(m)
            if m_copy["backmark"] == "31":
                m_copy["length_mm"] = m_copy["length_mm"] + 5.0  # +5 mm exceeds 2.0 mm tol
            tampered_members.append(m_copy)
        tampered_result = evaluate_regression_gates(
            design_input={"members": tampered_members},
            reference_data=self.ref_data,
            length_tol_mm=2.0,
        )
        mem31 = [r for r in tampered_result["scorecard"] if r["backmark"] == "31"][0]
        self.assertEqual(mem31["gate_b_section_length"], "FAIL")

    def test_gate_c_quantity_matching_authoritative_metadata(self):
        """Gate C: All 26 reference members have verified quantities matching Section 33."""
        expected_quantities = {
            "21": 4, "22H": 8, "23H": 4, "24H": 1, "25H": 1,
            "26H": 2, "27H": 2, "28H": 4, "29H": 8, "30": 8,
            "31": 2, "32": 2, "33": 2, "34": 2, "35": 2, "36": 2,
            "37": 2, "38": 2, "39H": 2, "40H": 2, "41": 2, "42": 2,
            "43": 2, "44": 4, "45": 2, "46": 4,
        }

        result = evaluate_regression_gates(
            design_input=self.conn_design,
            reference_data=self.ref_data,
        )
        gate_c_stats = result["summary"]["gates"]["gate_c_quantity"]
        self.assertEqual(gate_c_stats["pass"], 26)
        self.assertEqual(gate_c_stats["fail"], 0)

        # Check individual verified quantities in scorecard
        ref_rows = {r["backmark"]: r for r in result["scorecard"] if r["is_reference"]}
        for mark, exp_qty in expected_quantities.items():
            self.assertEqual(ref_rows[mark]["gate_c_quantity"], "PASS")

        # Negative test: baseline quantity error (e.g. qty=2 instead of 4 for member 21)
        tampered_members = []
        for m in self.conn_design["members"]:
            m_copy = dict(m)
            if m_copy["backmark"] == "21":
                m_copy["qty"] = 2  # Prior baseline flaw
            tampered_members.append(m_copy)
        tampered_result = evaluate_regression_gates(
            design_input={"members": tampered_members},
            reference_data=self.ref_data,
        )
        mem21 = [r for r in tampered_result["scorecard"] if r["backmark"] == "21"][0]
        self.assertEqual(mem21["gate_c_quantity"], "FAIL")

    def test_gate_d_holes_diagnostic_cases_37_44_45_46(self):
        """Gate D: Verified hole count, diameter classes, and positions for 37, 44, 45, 46."""
        result = evaluate_regression_gates(
            design_input=self.conn_design,
            reference_data=self.ref_data,
            position_tol_mm=2.0,
        )
        gate_d_stats = result["summary"]["gates"]["gate_d_holes"]
        self.assertEqual(gate_d_stats["pass"], 4)
        self.assertEqual(gate_d_stats["fail"], 0)
        self.assertEqual(gate_d_stats["not_applicable"], 22)

        ref_rows = {r["backmark"]: r for r in result["scorecard"] if r["is_reference"]}
        for mark in ["37", "44", "45", "46"]:
            self.assertEqual(ref_rows[mark]["gate_d_holes"], "PASS")

        # Negative test: incorrect hole pitch (e.g. generic pitch on member 44) fails Gate D
        tampered_members = []
        for m in self.conn_design["members"]:
            m_copy = dict(m)
            if m_copy["backmark"] == "44":
                # Replace with generic 40 mm pitch sequence
                m_copy["ends"] = {
                    "E1": {"holes": [{"along_mm": 35 + i * 40, "hole_diameter_mm": 13.5, "nominal_bolt_diameter_mm": 12} for i in range(8)]}
                }
            tampered_members.append(m_copy)
        tampered_result = evaluate_regression_gates(
            design_input={"members": tampered_members},
            reference_data=self.ref_data,
            position_tol_mm=2.0,
        )
        mem44 = [r for r in tampered_result["scorecard"] if r["backmark"] == "44"][0]
        self.assertEqual(mem44["gate_d_holes"], "FAIL")

    def test_gate_e_dimensions_chains_and_pitch_intervals(self):
        """Gate E: Verified dimension chains (overall L, end distances e1, e2, pitch intervals)."""
        result = evaluate_regression_gates(
            design_input=self.conn_design,
            reference_data=self.ref_data,
            length_tol_mm=2.0,
            position_tol_mm=2.0,
        )
        gate_e_stats = result["summary"]["gates"]["gate_e_dimensions"]
        self.assertEqual(gate_e_stats["pass"], 4)
        self.assertEqual(gate_e_stats["fail"], 0)
        self.assertEqual(gate_e_stats["not_applicable"], 22)

        ref_rows = {r["backmark"]: r for r in result["scorecard"] if r["is_reference"]}
        for mark in ["37", "44", "45", "46"]:
            self.assertEqual(ref_rows[mark]["gate_e_dimensions"], "PASS")

        # Directly verify multi-tier dimension interval summation for all diagnostic cases
        for mark in ["37", "44", "45", "46"]:
            m_data = [m for m in self.conn_design["members"] if m["backmark"] == mark][0]
            ref_m = self.ref_data["reference_members"][mark]
            holes = []
            for e in m_data.get("ends", {}).values():
                holes.extend(e.get("holes", []))
            cand_len = float(m_data["length_mm"])
            chains = build_dimension_chains(cand_len, holes, gauge_mm=ref_m.get("dimension_chain", {}).get("gauge_mm"))
            level1 = chains[0]
            sum_intervals = sum(dim.value_mm for dim in level1)
            self.assertAlmostEqual(sum_intervals, cand_len, places=2)
            self.assertEqual(chains[1][0].value_mm, cand_len)

    def test_gate_f_section_end_view_all_profile_families(self):
        """Gate F: Verified section profile families (Angle, Flat, HT_Angle, HT_Flat) render correctly."""
        # 1. Test SectionType classification
        self.assertEqual(classify_section("L90x90x6"), SectionType.ANGLE)
        self.assertEqual(classify_section("4 thk x45"), SectionType.FLAT)
        self.assertEqual(classify_section("FLAT 4x45"), SectionType.FLAT)
        self.assertEqual(classify_section("HTL150x150x12"), SectionType.HT_ANGLE)
        self.assertEqual(classify_section("HT8 thk x154"), SectionType.HT_FLAT)

        # 2. Test rendering for each profile family
        families = [
            ("21", "L90x90x6", "ANGLE"),
            ("30", "4 thk x94", "FLAT"),
            ("26H", "HTL150x150x12", "ANGLE"),
            ("22H", "HT8 thk x154", "FLAT"),
        ]

        doc = ezdxf.new()
        msp = doc.modelspace()

        for mark, sec_str, kind in families:
            info = _parse_section(sec_str)
            self.assertEqual(info["kind"], kind)
            end_renderer = EndSectionRenderer(info)
            res = end_renderer.render(msp, (0, 0), scale=0.1)
            self.assertIn("kind", res)

        # 3. Test Gate F on entire reference dataset
        result = evaluate_regression_gates(
            design_input=self.conn_design,
            reference_data=self.ref_data,
        )
        gate_f_stats = result["summary"]["gates"]["gate_f_section_view"]
        self.assertEqual(gate_f_stats["pass"], 26)
        self.assertEqual(gate_f_stats["fail"], 0)

    def test_gate_h_no_false_positives_non_reference_not_validated(self):
        """Gate H: Never mark drawing as MATCH merely because metadata matches; 11 non-refs NOT_VALIDATED."""
        result = evaluate_regression_gates(
            design_input=self.conn_design,
            reference_data=self.ref_data,
        )
        gate_h_stats = result["summary"]["gates"]["gate_h_no_false_positives"]
        self.assertEqual(gate_h_stats["pass"], 37)
        self.assertEqual(gate_h_stats["fail"], 0)

        # All 11 non-reference members must be explicitly NOT_VALIDATED
        non_ref_rows = [r for r in result["scorecard"] if not r["is_reference"]]
        self.assertEqual(len(non_ref_rows), 11)
        for r in non_ref_rows:
            self.assertEqual(r["validation_status"], "NOT_VALIDATED")
            self.assertEqual(r["gate_h_no_false_positives"], "PASS")

        # Reference members without hole detail must NOT be marked MATCH
        ref_rows = {r["backmark"]: r for r in result["scorecard"] if r["is_reference"]}
        # Member 21 has matching metadata but no hole detailing -> METADATA_MATCH_ONLY
        self.assertEqual(ref_rows["21"]["validation_status"], "METADATA_MATCH_ONLY")
        # Diagnostic cases 37, 44, 45, 46 have full fabrication verification -> VALIDATED_REFERENCE
        for mark in ["37", "44", "45", "46"]:
            self.assertEqual(ref_rows[mark]["validation_status"], "VALIDATED_REFERENCE")

    def test_regression_report_file_generation_json_and_csv(self):
        """Verify write_regression_report writes complete regression_report.json and regression_report.csv."""
        result = evaluate_regression_gates(
            design_input=self.conn_design,
            reference_data=self.ref_data,
        )
        self.assertTrue(result["summary"]["overall_pass"])

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            write_regression_report(result, out_dir)

            json_file = out_dir / "regression_report.json"
            csv_file = out_dir / "regression_report.csv"
            self.assertTrue(json_file.exists())
            self.assertTrue(csv_file.exists())

            # Verify JSON content
            data = json.loads(json_file.read_text(encoding="utf-8"))
            self.assertEqual(data["summary"]["members_total"], 37)
            self.assertEqual(data["summary"]["reference_drawings"], 26)
            self.assertEqual(data["summary"]["not_validated"], 11)
            self.assertTrue(data["summary"]["overall_pass"])

            # Verify CSV content
            with open(csv_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                self.assertEqual(len(rows), 37)
                marks = {r["backmark"] for r in rows}
                self.assertIn("21", marks)
                self.assertIn("44", marks)
                self.assertIn("49H", marks)

    def test_gate_b_corrupted_inputs_fail_gracefully(self):
        """Gate B must gracefully fail rather than throwing unhandled exceptions on malformed length or section."""
        # Non-numeric string length
        res1 = evaluate_regression_gates(
            design_input={"members": [{"backmark": "21", "section": "L90x90x6", "length_mm": "corrupted", "qty": 4}]},
            reference_data=self.ref_data,
        )
        mem21_1 = [r for r in res1["scorecard"] if r["backmark"] == "21"][0]
        self.assertEqual(mem21_1["gate_b_section_length"], "FAIL")

        # None length
        res2 = evaluate_regression_gates(
            design_input={"members": [{"backmark": "21", "section": "L90x90x6", "length_mm": None, "qty": 4}]},
            reference_data=self.ref_data,
        )
        mem21_2 = [r for r in res2["scorecard"] if r["backmark"] == "21"][0]
        self.assertEqual(mem21_2["gate_b_section_length"], "FAIL")

        # Empty section string
        res3 = evaluate_regression_gates(
            design_input={"members": [{"backmark": "21", "section": "", "length_mm": 1849.0, "qty": 4}]},
            reference_data=self.ref_data,
        )
        mem21_3 = [r for r in res3["scorecard"] if r["backmark"] == "21"][0]
        self.assertEqual(mem21_3["gate_b_section_length"], "FAIL")

    def test_section_normalization_permutations_and_thk_variants(self):
        """Verify _normalize_section_str normalizes dimension order and THK variants correctly."""
        self.assertEqual(_normalize_section_str("8 thk x 154"), _normalize_section_str("154 thk x 8"))
        self.assertEqual(_normalize_section_str("4 THK. x 45"), "FLAT_4X45")
        self.assertEqual(_normalize_section_str("HT8 thk x 154"), "HT_FLAT_8X154")
        self.assertEqual(_normalize_section_str("ISA 65x65x6"), "L_65X65X6")
        self.assertEqual(_normalize_section_str("HTL 60x60x5"), "HTL_60X60X5")

    def test_gate_d_corrupted_holes_fail_gracefully(self):
        """Gate D must fail gracefully without throwing exceptions on non-numeric hole coordinates or diameters."""
        # Malformed along_mm
        res1 = evaluate_regression_gates(
            design_input={"members": [{
                "backmark": "37", "length_mm": 2294.0, "section": "L50x50x5", "qty": 2,
                "ends": {"E1": {"holes": [
                    {"along_mm": "invalid_pos", "hole_diameter_mm": 11.5},
                    {"along_mm": 86.0, "hole_diameter_mm": 11.5},
                    {"along_mm": 327.0, "hole_diameter_mm": 11.5},
                    {"along_mm": 1197.0, "hole_diameter_mm": 11.5},
                    {"along_mm": 2213.0, "hole_diameter_mm": 11.5},
                    {"along_mm": 2267.0, "hole_diameter_mm": 13.5},
                ]}}
            }]},
            reference_data=self.ref_data,
        )
        mem37_1 = [r for r in res1["scorecard"] if r["backmark"] == "37"][0]
        self.assertEqual(mem37_1["gate_d_holes"], "FAIL")

        # Malformed hole_diameter_mm
        res2 = evaluate_regression_gates(
            design_input={"members": [{
                "backmark": "37", "length_mm": 2294.0, "section": "L50x50x5", "qty": 2,
                "ends": {"E1": {"holes": [
                    {"along_mm": 27.0, "hole_diameter_mm": "M10_bad"},
                    {"along_mm": 86.0, "hole_diameter_mm": 11.5},
                    {"along_mm": 327.0, "hole_diameter_mm": 11.5},
                    {"along_mm": 1197.0, "hole_diameter_mm": 11.5},
                    {"along_mm": 2213.0, "hole_diameter_mm": 11.5},
                    {"along_mm": 2267.0, "hole_diameter_mm": 13.5},
                ]}}
            }]},
            reference_data=self.ref_data,
        )
        mem37_2 = [r for r in res2["scorecard"] if r["backmark"] == "37"][0]
        self.assertEqual(mem37_2["gate_d_holes"], "FAIL")


if __name__ == "__main__":
    unittest.main()

