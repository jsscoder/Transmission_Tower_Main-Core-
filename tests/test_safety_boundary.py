"""Tests for Milestone 1: Safety Boundary & Canonical Domain.

Verifies:
1. Running without approved engineering input blocks fabrication generation and does not synthesize fake holes.
2. Missing quantity produces BLOCKED/MISSING_QTY and is never defaulted to 2 or 1 in BOM or inventory.
3. Empty hole schedules produce BLOCKED/MISSING_FABRICATION_HOLES rather than false buildable reports.
4. Dimension chain rule: last hole coordinate != member overall length (guarding against shop_drawing.py:677 bug).
5. Canonical domain model serialization/deserialization integrity.
6. Ground truth fixtures are not used as runtime fallback in production.
"""
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ezdxf
from bom import build_bom, write_bom
from domain.enums import EndId, SectionType, SourceKind, ValidationState
from domain.models import (
    DimensionItem,
    Hole,
    Member,
    MemberEnd,
    ProvenanceRecord,
    ShopDrawingModel,
    TitleBlockData,
    build_dimension_chains,
    classify_section,
)
from inference import initialize_design_input, run_pipeline
from shop_drawing import (
    _draw_dimension_chain,
    build_canonical_shop_json,
    generate_member_dxf,
    validate_member_design,
)
from shop_reporting import inventory_from_schedule
from validate_design import validate


def _close_log_handlers():
    """Ensure Windows file locks on pipeline.log are released."""
    logger = logging.getLogger("tower_inference")
    for handler in logger.handlers[:]:
        try:
            handler.close()
        except Exception:
            pass
        logger.removeHandler(handler)


class SafetyBoundaryTests(unittest.TestCase):

    def tearDown(self):
        _close_log_handlers()

    def test_initialize_design_input_does_not_synthesize_fake_data(self):
        """Verify initialize_design_input does not fake hole coordinates or default qty to 2 for parts 37, 44, 45, 46."""
        schedule = {
            "37": {"section": "L50x50x5", "length_mm": 2294},
            "44": {"section": "FLAT 4x45", "length_mm": 1128},
            "45": {"section": "FLAT 4x45", "length_mm": 1280},
            "46": {"section": "FLAT 4x45", "length_mm": 664},
            "99": {"section": "L60x60x5", "length_mm": 1500},
        }
        with tempfile.TemporaryDirectory() as td:
            out_json = Path(td) / "template.json"
            data = initialize_design_input(schedule, out_json)

            self.assertTrue(out_json.exists())
            self.assertEqual(data["engineering_source"], "UNPOPULATED_TEMPLATE_FROM_SCHEDULE")

            for m in data["members"]:
                # Quantity must be None, NEVER defaulted to 2
                self.assertIsNone(m["qty"], f"Member {m['backmark']} should have qty=None")
                # Holes must be empty, NEVER synthesized
                e1_holes = m["ends"]["E1"]["holes"]
                e2_holes = m["ends"]["E2"]["holes"]
                self.assertEqual(e1_holes, [], f"Member {m['backmark']} E1 must not synthesize holes")
                self.assertEqual(e2_holes, [], f"Member {m['backmark']} E2 must not synthesize holes")

    def test_missing_quantity_produces_blocked_and_bom_missing(self):
        """Verify missing quantity produces BLOCKED/MISSING_QTY, never defaulting to 2 or 1."""
        schedule = {
            "44": {"section": "FLAT 4x45", "length_mm": 1128},
        }
        design_with_missing_qty = {
            "job": "TEST_JOB",
            "members": [
                {
                    "backmark": "44",
                    "section": "FLAT 4x45",
                    "length_mm": 1128,
                    "qty": None,  # explicitly missing
                    "ends": {
                        "E1": {
                            "holes": [
                                {"along_mm": 35, "hole_diameter_mm": 13.5, "nominal_bolt_diameter_mm": 12}
                            ]
                        }
                    }
                }
            ]
        }

        # 1. Inventory check: must be BLOCKED with MISSING_QTY
        inv = inventory_from_schedule(schedule, design_with_missing_qty)
        self.assertEqual(inv["currently_buildable_with_design_input"], 0)
        self.assertEqual(inv["blocked_count"], 1)
        blocked_entry = inv["blocked"][0]
        self.assertEqual(blocked_entry["backmark"], "44")
        self.assertIn("MISSING_QTY", blocked_entry["reason"])

        # 2. BOM check: must mark quantity_status as MISSING and not calculate total_qty using 1 or 2
        bom_rows = build_bom(design_with_missing_qty)
        self.assertEqual(len(bom_rows), 1)
        row = bom_rows[0]
        self.assertEqual(row["quantity_status"], "MISSING")
        self.assertIsNone(row["member_qty"], "member_qty must be None when quantity is missing")
        self.assertIsNone(row["total_qty"], "total_qty must be None when quantity is missing, never defaulting to 1 or 2")

        # 3. Validation check: validate_design must report error for missing quantity
        val_res = validate(design_with_missing_qty)
        self.assertFalse(val_res["valid"])
        self.assertTrue(any("MISSING_QTY" in e for e in val_res["errors"]))

    def test_empty_hole_schedule_produces_blocked_missing_fabrication_holes(self):
        """Verify empty hole schedule produces BLOCKED/MISSING_FABRICATION_HOLES, eliminating false buildable reports."""
        schedule = {
            "21": {"section": "L90x90x6", "length_mm": 1849},
        }
        design_with_empty_holes = {
            "job": "TEST_JOB",
            "members": [
                {
                    "backmark": "21",
                    "section": "L90x90x6",
                    "length_mm": 1849,
                    "qty": 2,
                    "ends": {
                        "E1": {"holes": []},
                        "E2": {"holes": []}
                    }
                }
            ]
        }

        inv = inventory_from_schedule(schedule, design_with_empty_holes)
        self.assertEqual(inv["currently_buildable_with_design_input"], 0)
        self.assertEqual(inv["blocked_count"], 1)
        self.assertEqual(inv["blocked"][0]["backmark"], "21")
        self.assertIn("MISSING_FABRICATION_HOLES", inv["blocked"][0]["reason"])

        val_res = validate(design_with_empty_holes)
        self.assertFalse(val_res["valid"])
        self.assertTrue(any("MISSING_FABRICATION_HOLES" in e for e in val_res["errors"]))

    def test_running_without_engineering_input_blocks_generation(self):
        """Verify pipeline blocks fabrication generation when no engineering input is supplied."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out"
            dxf_p = Path(td) / "test.dxf"
            doc = ezdxf.new()
            doc.layers.add("3_Members")
            msp = doc.modelspace()
            msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "3_Members"})
            doc.saveas(dxf_p)

            mock_schedule = {"44": {"section": "FLAT 4x45", "length_mm": 1128, "count": 1, "inferred": False}}
            mock_topology = {"stats": {"mapped_members": 1, "joints": 0, "groups_with_joint_candidates": 0}}
            mock_resolution = {"stats": {"auto_candidate": 0, "review": 0, "reject": 0}}

            with mock.patch("tower.extract_member_schedule", return_value=mock_schedule), \
                 mock.patch("tower.write_schedule", return_value=[{"backmark": "44"}]), \
                 mock.patch("tower.render_locator_crops", return_value=[]), \
                 mock.patch("inference.extract_bolt_callouts", return_value=[]), \
                 mock.patch("inference.group_stacked_callouts", return_value=[]), \
                 mock.patch("inference.write_callout_outputs"), \
                 mock.patch("inference.associate", return_value=[]), \
                 mock.patch("inference.write_associations", return_value=[]), \
                 mock.patch("inference.extract_member_evidence", return_value={}), \
                 mock.patch("inference.write_shop_outputs", return_value=[]), \
                 mock.patch("inference.groups_as_dicts", return_value=[]), \
                 mock.patch("inference.build_topology", return_value=mock_topology), \
                 mock.patch("inference.write_topology"), \
                 mock.patch("inference.resolve", return_value=mock_resolution), \
                 mock.patch("inference.write_resolved"):

                summary = run_pipeline(
                    str(dxf_p),
                    out_dir=str(out_dir),
                    generate=True,
                    engineering_input=None,
                    design_input=None,
                )

                self.assertEqual(summary["generation_status"], "BLOCKED")
                self.assertEqual(summary["generation_reason"], "MISSING_APPROVED_ENGINEERING_INPUT")
                self.assertEqual(summary["generated_drawings"], 0)
                self.assertEqual(summary["currently_buildable_shop_drawings"], 0)

                status_file = out_dir / "shop_generation_status.json"
                self.assertTrue(status_file.exists())
                status_data = json.loads(status_file.read_text(encoding="utf-8"))
                self.assertEqual(status_data["status"], "BLOCKED")
                self.assertEqual(status_data["reason"], "MISSING_APPROVED_ENGINEERING_INPUT")

                # Verify no shop drawing DXFs were generated
                shop_dxfs = list((out_dir / "shop_drawings").glob("*.dxf"))
                self.assertEqual(len(shop_dxfs), 0)

            _close_log_handlers()

    def test_dimension_chain_rule_overall_length_guard(self):
        """Verify dimension chain rule: last hole coordinate != member overall length, guarding against shop_drawing.py:677."""
        member_length = 1128.0
        # Member 44 holes: 4 at E1 (35..155), 4 at E2 (973..1093)
        holes = [
            {"along_mm": 35.0, "hole_diameter_mm": 13.5},
            {"along_mm": 75.0, "hole_diameter_mm": 13.5},
            {"along_mm": 115.0, "hole_diameter_mm": 13.5},
            {"along_mm": 155.0, "hole_diameter_mm": 13.5},
            {"along_mm": 973.0, "hole_diameter_mm": 13.5},
            {"along_mm": 1013.0, "hole_diameter_mm": 13.5},
            {"along_mm": 1053.0, "hole_diameter_mm": 13.5},
            {"along_mm": 1093.0, "hole_diameter_mm": 13.5},
        ]
        last_hole_coord = 1093.0
        self.assertNotEqual(last_hole_coord, member_length)

        chains = build_dimension_chains(member_length, holes)
        self.assertEqual(len(chains), 2)
        incremental_chain, overall_chain = chains[0], chains[1]

        # Level 1 incremental chain must sum exactly to member_length
        inc_sum = sum(item.value_mm for item in incremental_chain)
        self.assertAlmostEqual(inc_sum, member_length, places=5)

        # Level 2 overall chain must have value_mm == member_length, NOT last_hole_coord
        self.assertEqual(len(overall_chain), 1)
        overall_item = overall_chain[0]
        self.assertEqual(overall_item.value_mm, member_length)
        self.assertNotEqual(overall_item.value_mm, last_hole_coord, "Overall dimension must not equal last hole coordinate!")
        self.assertEqual(overall_item.label, f"OVERALL = {member_length:g} mm")

        # Test shop_drawing._draw_dimension_chain with overall_length_mm
        doc = ezdxf.new()
        msp = doc.modelspace()
        positions = [h["along_mm"] for h in holes]
        _draw_dimension_chain(msp, positions, 140, 80, scale=1.0, overall_length_mm=member_length)

        text_entities = [e.dxf.text for e in msp.query("TEXT")]
        self.assertIn(f"OVERALL = {member_length:g} mm", text_entities)
        self.assertNotIn(f"OVERALL = {last_hole_coord:g} mm", text_entities)

        # Test that hole landing at or beyond member end violates dimension chain rule
        invalid_holes = list(holes) + [{"along_mm": 1128.0, "hole_diameter_mm": 13.5}]
        with self.assertRaises(ValueError):
            build_dimension_chains(member_length, invalid_holes)

    def test_canonical_domain_model_serialization(self):
        """Verify round-trip serialization of canonical domain models."""
        prov = ProvenanceRecord(
            source_kind=SourceKind.DESIGN_INPUT,
            source_id="connection_design.json",
            entity_id="44",
            confidence=1.0,
            rule_id="IS_802",
            notes="Approved production input",
        )
        hole = Hole(
            hole_id="H_44_E1_0",
            member_backmark="44",
            end="E1",
            along_mm=35.0,
            transverse_mm=0.0,
            diameter_mm=13.5,
            nominal_bolt_diameter_mm=12.0,
            bolt_size="M12",
            edge_distance_mm=25.0,
            provenance=prov,
        )
        end = MemberEnd(
            end_id="E1",
            coordinate=(0.0, 0.0),
            joint_id="J1",
            connection_group_id="G1",
            holes=[hole],
        )
        member = Member(
            backmark="44",
            section="FLAT 4x45",
            section_type=SectionType.FLAT,
            length_mm=1128.0,
            quantity=4,
            quantity_source="SCHEDULE",
            ends={"E1": end},
            status=ValidationState.READY,
            provenance=prov,
        )
        title_block = TitleBlockData(
            job_no="WO_429",
            drawing_id="429B44",
            backmark="44",
            section="FLAT 4x45",
            length_mm=1128.0,
            quantity=4,
            standard="IS_802",
            date="2026-09-15",
            rev="0",
            status="APPROVED",
        )
        chains = build_dimension_chains(1128.0, [hole])
        drawing = ShopDrawingModel(
            drawing_id="429B44",
            backmark="44",
            member=member,
            holes=[hole],
            dimension_chains=chains,
            end_section={"kind": "FLAT", "width_mm": 45, "thickness_mm": 4},
            title_block=title_block,
            status=ValidationState.READY,
            provenance=prov,
        )

        # Serialize to dict and JSON
        d = drawing.to_dict()
        json_str = json.dumps(d, indent=2)
        self.assertIsInstance(json_str, str)

        # Deserialize back to model
        loaded_d = json.loads(json_str)
        rebuilt = ShopDrawingModel.from_dict(loaded_d)

        self.assertEqual(rebuilt.drawing_id, "429B44")
        self.assertEqual(rebuilt.backmark, "44")
        self.assertEqual(rebuilt.member.section_type, SectionType.FLAT)
        self.assertEqual(rebuilt.member.length_mm, 1128.0)
        self.assertEqual(rebuilt.member.quantity, 4)
        self.assertEqual(rebuilt.status, ValidationState.READY)
        self.assertEqual(len(rebuilt.holes), 1)
        self.assertEqual(rebuilt.holes[0].nominal_bolt_diameter_mm, 12.0)
        self.assertEqual(rebuilt.holes[0].bolt_size, "M12")
        self.assertEqual(rebuilt.title_block.standard, "IS_802")
        self.assertEqual(len(rebuilt.dimension_chains), 2)
        self.assertEqual(rebuilt.dimension_chains[1][0].value_mm, 1128.0)

    def test_bom_with_invalid_and_missing_quantities(self):
        """Verify BOM rejects invalid quantities and never defaults to 1 or 2."""
        design = {
            "members": [
                {
                    "backmark": "101",
                    "section": "L50x50x5",
                    "length_mm": 1000,
                    "qty": 0,  # invalid
                    "ends": {"E1": {"holes": [{"nominal_bolt_diameter_mm": 12, "diameter_mm": 13.5, "along_mm": 35}]}}
                },
                {
                    "backmark": "102",
                    "section": "L50x50x5",
                    "length_mm": 1000,
                    "qty": -4,  # invalid negative
                    "ends": {"E1": {"holes": [{"nominal_bolt_diameter_mm": 12, "diameter_mm": 13.5, "along_mm": 35}]}}
                },
                {
                    "backmark": "103",
                    "section": "L50x50x5",
                    "length_mm": 1000,
                    "qty": "not_a_number",  # non-numeric
                    "ends": {"E1": {"holes": [{"nominal_bolt_diameter_mm": 12, "diameter_mm": 13.5, "along_mm": 35}]}}
                },
                {
                    "backmark": "104",
                    "section": "L50x50x5",
                    "length_mm": 1000,
                    "qty": 3,  # valid
                    "ends": {"E1": {"holes": [{"nominal_bolt_diameter_mm": 12, "diameter_mm": 13.5, "along_mm": 35}]}}
                }
            ]
        }
        rows = build_bom(design)
        self.assertEqual(len(rows), 4)

        row_101 = next(r for r in rows if r["backmark"] == "101")
        self.assertEqual(row_101["quantity_status"], "INVALID")
        self.assertIsNone(row_101["member_qty"])
        self.assertIsNone(row_101["total_qty"])

        row_102 = next(r for r in rows if r["backmark"] == "102")
        self.assertEqual(row_102["quantity_status"], "INVALID")
        self.assertIsNone(row_102["member_qty"])
        self.assertIsNone(row_102["total_qty"])

        row_103 = next(r for r in rows if r["backmark"] == "103")
        self.assertEqual(row_103["quantity_status"], "INVALID")
        self.assertIsNone(row_103["member_qty"])
        self.assertIsNone(row_103["total_qty"])

        row_104 = next(r for r in rows if r["backmark"] == "104")
        self.assertEqual(row_104["quantity_status"], "VALID")
        self.assertEqual(row_104["member_qty"], 3)
        self.assertEqual(row_104["total_qty"], 3)

    def test_section_classification_and_enums(self):
        """Verify section classification and enum helper conversions."""
        self.assertEqual(classify_section("L50x50x5"), SectionType.ANGLE)
        self.assertEqual(classify_section("L90X90X6"), SectionType.ANGLE)
        self.assertEqual(classify_section("HTL45x45x5"), SectionType.HT_ANGLE)
        self.assertEqual(classify_section("FLAT 4x45"), SectionType.FLAT)
        self.assertEqual(classify_section("4 thk x 45"), SectionType.FLAT)
        self.assertEqual(classify_section("4x45"), SectionType.FLAT)
        self.assertEqual(classify_section("HT 8 thk x 154"), SectionType.HT_FLAT)
        self.assertEqual(classify_section("PL 10"), SectionType.PLATE)
        self.assertEqual(classify_section("UNKNOWN_FOO"), SectionType.UNKNOWN)
        self.assertEqual(classify_section(None), SectionType.UNKNOWN)

        self.assertEqual(SectionType.from_str("ANGLE"), SectionType.ANGLE)
        self.assertEqual(SectionType.from_str("bogus"), SectionType.UNKNOWN)
        self.assertEqual(ValidationState.from_str("BLOCKED"), ValidationState.BLOCKED)
        self.assertEqual(ValidationState.from_str("bogus"), ValidationState.NOT_VALIDATED)
        self.assertEqual(SourceKind.from_str("DESIGN_INPUT"), SourceKind.DESIGN_INPUT)
        self.assertEqual(EndId.from_str("E1"), EndId.E1)

    def test_validate_design_positive_and_negative(self):
        """Verify validate_design handles valid inputs and catches structural defects."""
        valid_input = {
            "members": [
                {
                    "backmark": "44",
                    "section": "FLAT 4x45",
                    "length_mm": 1128,
                    "qty": 4,
                    "ends": {
                        "E1": {
                            "pattern": {
                                "count": 2,
                                "start_from_end_mm": 35,
                                "pitch_mm": 40,
                                "hole_diameter_mm": 13.5,
                                "edge_distance_mm": 25,
                                "nominal_bolt_diameter_mm": 12,
                            }
                        }
                    }
                }
            ]
        }
        res = validate(valid_input)
        self.assertTrue(res["valid"])
        self.assertEqual(res["error_count"], 0)

        # Hole exceeds member length
        invalid_hole_pos = {
            "members": [
                {
                    "backmark": "44",
                    "section": "FLAT 4x45",
                    "length_mm": 100,
                    "qty": 2,
                    "ends": {
                        "E1": {
                            "holes": [
                                {"along_mm": 150, "hole_diameter_mm": 13.5}
                            ]
                        }
                    }
                }
            ]
        }
        res2 = validate(invalid_hole_pos)
        self.assertFalse(res2["valid"])
        self.assertTrue(any("exceeds or equals member length" in e for e in res2["errors"]))

    def test_explicit_engineering_input_generates_successfully(self):
        """Verify pipeline generates drawings when explicit engineering input is supplied."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out"
            dxf_p = Path(td) / "test.dxf"
            doc = ezdxf.new()
            doc.layers.add("3_Members")
            msp = doc.modelspace()
            msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "3_Members"})
            doc.saveas(dxf_p)

            design_p = Path(td) / "approved_design.json"
            design_data = {
                "job": "WO_429",
                "engineering_source": "APPROVED_BY_CHIEF_ENGINEER",
                "members": [
                    {
                        "backmark": "44",
                        "section": "FLAT 4x45",
                        "length_mm": 1128,
                        "qty": 4,
                        "ends": {
                            "E1": {
                                "pattern": {
                                    "count": 4,
                                    "start_from_end_mm": 35,
                                    "pitch_mm": 40,
                                    "hole_diameter_mm": 13.5,
                                    "nominal_bolt_diameter_mm": 12,
                                    "edge_distance_mm": 25,
                                }
                            }
                        }
                    }
                ]
            }
            design_p.write_text(json.dumps(design_data, indent=2), encoding="utf-8")

            mock_schedule = {"44": {"section": "FLAT 4x45", "length_mm": 1128, "count": 1, "inferred": False}}
            mock_topology = {"stats": {"mapped_members": 1, "joints": 0, "groups_with_joint_candidates": 0}}
            mock_resolution = {"stats": {"auto_candidate": 0, "review": 0, "reject": 0}}

            with mock.patch("tower.extract_member_schedule", return_value=mock_schedule), \
                 mock.patch("tower.write_schedule", return_value=[{"backmark": "44"}]), \
                 mock.patch("tower.render_locator_crops", return_value=[]), \
                 mock.patch("inference.extract_bolt_callouts", return_value=[]), \
                 mock.patch("inference.group_stacked_callouts", return_value=[]), \
                 mock.patch("inference.write_callout_outputs"), \
                 mock.patch("inference.associate", return_value=[]), \
                 mock.patch("inference.write_associations", return_value=[]), \
                 mock.patch("inference.extract_member_evidence", return_value={}), \
                 mock.patch("inference.write_shop_outputs", return_value=[]), \
                 mock.patch("inference.groups_as_dicts", return_value=[]), \
                 mock.patch("inference.build_topology", return_value=mock_topology), \
                 mock.patch("inference.write_topology"), \
                 mock.patch("inference.resolve", return_value=mock_resolution), \
                 mock.patch("inference.write_resolved"):

                summary = run_pipeline(
                    str(dxf_p),
                    out_dir=str(out_dir),
                    generate=True,
                    engineering_input=str(design_p),
                )

                self.assertEqual(summary["generation_status"], "COMPLETED")
                self.assertIsNone(summary["generation_reason"])
                self.assertEqual(summary["generated_drawings"], 1)
                self.assertEqual(summary["currently_buildable_shop_drawings"], 1)
                self.assertEqual(summary["bom_rows"], 1)

                # Check DXF generated
                gen_dxf = out_dir / "shop_drawings" / "429B44.dxf"
                self.assertTrue(gen_dxf.exists())

                # Check status record
                status_file = out_dir / "shop_generation_status.json"
                self.assertTrue(status_file.exists())
                status_data = json.loads(status_file.read_text(encoding="utf-8"))
                self.assertEqual(status_data["status"], "COMPLETED")

            _close_log_handlers()

    def test_fixtures_relegated_to_test_suites(self):
        """Verify production inference does not fallback to loading fixtures/shop_ground_truth.json."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out"
            dxf_p = Path(td) / "test.dxf"
            doc = ezdxf.new()
            doc.layers.add("3_Members")
            msp = doc.modelspace()
            msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "3_Members"})
            doc.saveas(dxf_p)

            mock_schedule = {"44": {"section": "FLAT 4x45", "length_mm": 1128, "count": 1, "inferred": False}}
            mock_topology = {"stats": {"mapped_members": 1, "joints": 0, "groups_with_joint_candidates": 0}}
            mock_resolution = {"stats": {"auto_candidate": 0, "review": 0, "reject": 0}}

            with mock.patch("tower.extract_member_schedule", return_value=mock_schedule), \
                 mock.patch("tower.write_schedule", return_value=[{"backmark": "44"}]), \
                 mock.patch("tower.render_locator_crops", return_value=[]), \
                 mock.patch("inference.extract_bolt_callouts", return_value=[]), \
                 mock.patch("inference.group_stacked_callouts", return_value=[]), \
                 mock.patch("inference.write_callout_outputs"), \
                 mock.patch("inference.associate", return_value=[]), \
                 mock.patch("inference.write_associations", return_value=[]), \
                 mock.patch("inference.extract_member_evidence", return_value={}), \
                 mock.patch("inference.write_shop_outputs", return_value=[]), \
                 mock.patch("inference.groups_as_dicts", return_value=[]), \
                 mock.patch("inference.build_topology", return_value=mock_topology), \
                 mock.patch("inference.write_topology"), \
                 mock.patch("inference.resolve", return_value=mock_resolution), \
                 mock.patch("inference.write_resolved"):

                summary = run_pipeline(
                    str(dxf_p),
                    out_dir=str(out_dir),
                    generate=False,
                    reference_shop_json=None,
                    reference_shop_json_dir=None,
                )

                # Reference records must be 0 because ground truth fixture was NOT loaded automatically
                self.assertEqual(summary["reference_shop_json_records"], 0)

            _close_log_handlers()

    def test_generate_false_skips_generation_even_with_engineering_input(self):
        """Verify run_pipeline with generate=False strictly skips generation even if engineering_input is supplied."""
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td) / "out"
            dxf_p = Path(td) / "test.dxf"
            doc = ezdxf.new()
            doc.layers.add("3_Members")
            msp = doc.modelspace()
            msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "3_Members"})
            doc.saveas(dxf_p)

            design_p = Path(td) / "approved_design.json"
            design_data = {
                "job": "WO_429",
                "engineering_source": "APPROVED_BY_CHIEF_ENGINEER",
                "members": [
                    {
                        "backmark": "44",
                        "section": "FLAT 4x45",
                        "length_mm": 1128,
                        "qty": 4,
                        "ends": {
                            "E1": {
                                "pattern": {
                                    "count": 4,
                                    "start_from_end_mm": 35,
                                    "pitch_mm": 40,
                                    "hole_diameter_mm": 13.5,
                                    "nominal_bolt_diameter_mm": 12,
                                    "edge_distance_mm": 25,
                                }
                            }
                        }
                    }
                ]
            }
            design_p.write_text(json.dumps(design_data, indent=2), encoding="utf-8")

            mock_schedule = {"44": {"section": "FLAT 4x45", "length_mm": 1128, "count": 1, "inferred": False}}
            mock_topology = {"stats": {"mapped_members": 1, "joints": 0, "groups_with_joint_candidates": 0}}
            mock_resolution = {"stats": {"auto_candidate": 0, "review": 0, "reject": 0}}

            with mock.patch("tower.extract_member_schedule", return_value=mock_schedule), \
                 mock.patch("tower.write_schedule", return_value=[{"backmark": "44"}]), \
                 mock.patch("tower.render_locator_crops", return_value=[]), \
                 mock.patch("inference.extract_bolt_callouts", return_value=[]), \
                 mock.patch("inference.group_stacked_callouts", return_value=[]), \
                 mock.patch("inference.write_callout_outputs"), \
                 mock.patch("inference.associate", return_value=[]), \
                 mock.patch("inference.write_associations", return_value=[]), \
                 mock.patch("inference.extract_member_evidence", return_value={}), \
                 mock.patch("inference.write_shop_outputs", return_value=[]), \
                 mock.patch("inference.groups_as_dicts", return_value=[]), \
                 mock.patch("inference.build_topology", return_value=mock_topology), \
                 mock.patch("inference.write_topology"), \
                 mock.patch("inference.resolve", return_value=mock_resolution), \
                 mock.patch("inference.write_resolved"):

                summary = run_pipeline(
                    str(dxf_p),
                    out_dir=str(out_dir),
                    generate=False,
                    engineering_input=str(design_p),
                )

                self.assertEqual(summary["generation_status"], "SKIPPED")
                self.assertEqual(summary["generation_reason"], "NOT_REQUESTED")
                self.assertEqual(summary["generated_drawings"], 0)
                # Verify no shop drawing DXFs were generated
                shop_dxfs = list((out_dir / "shop_drawings").glob("*.dxf"))
                self.assertEqual(len(shop_dxfs), 0)

            _close_log_handlers()

    def test_member_domain_model_interoperability_with_generator(self):
        """Verify Member domain model serialization includes qty alias and interoperates with shop drawing generator."""
        hole = Hole(
            hole_id="H_44_E1_0",
            member_backmark="44",
            end="E1",
            along_mm=35.0,
            diameter_mm=13.5,
            nominal_bolt_diameter_mm=12.0,
            edge_distance_mm=25.0,
        )
        end = MemberEnd(end_id="E1", holes=[hole])
        member = Member(
            backmark="44",
            section="FLAT 4x45",
            section_type=SectionType.FLAT,
            length_mm=1128.0,
            quantity=4,
            quantity_source="SCHEDULE",
            ends={"E1": end},
            status=ValidationState.READY,
        )
        d = member.to_dict()
        # Verify both quantity and qty keys exist in serialized dict
        self.assertIn("quantity", d)
        self.assertIn("qty", d)
        self.assertEqual(d["quantity"], 4)
        self.assertEqual(d["qty"], 4)

        rules = {
            "standard": "IS_802",
            "hole_rules": [
                {"hole_diameter_mm": 13.5, "min_pitch_mm": 27, "min_edge_distance_mm": 22}
            ]
        }
        # validate_member_design must accept member dict directly without KeyError on qty
        holes = validate_member_design(d, rules)
        self.assertEqual(len(holes), 1)

        # validate_member_design must also accept domain Member instance directly
        holes_from_obj = validate_member_design(member, rules)
        self.assertEqual(len(holes_from_obj), 1)

        # generate_member_dxf must succeed with domain Member dict
        with tempfile.TemporaryDirectory() as td:
            out_dxf = Path(td) / "429B44.dxf"
            res = generate_member_dxf(d, rules, out_dxf)
            self.assertTrue(out_dxf.exists())
            self.assertEqual(res["qty"], 4)
            self.assertEqual(res["quantity"], 4)

    def test_shop_drawing_model_from_generated_shop_json(self):
        """Verify ShopDrawingModel.from_dict ingests the dictionary produced by build_canonical_shop_json without losing holes."""
        member_dict = {
            "backmark": "44",
            "section": "FLAT 4x45",
            "length_mm": 1128.0,
            "qty": 4,
            "ends": {
                "E1": {
                    "holes": [
                        {"along_mm": 35.0, "hole_diameter_mm": 13.5, "nominal_bolt_diameter_mm": 12.0, "edge_distance_mm": 25.0}
                    ]
                }
            }
        }
        holes = [
            {"end": "E1", "along_mm": 35.0, "hole_diameter_mm": 13.5, "nominal_bolt_diameter_mm": 12.0, "edge_distance_mm": 25.0}
        ]
        rules = {"standard": "IS_802"}
        canonical_json = build_canonical_shop_json(member_dict, holes, rules)

        self.assertIn("dimension_chains", canonical_json)
        self.assertIn("qty", canonical_json)
        self.assertIn("quantity", canonical_json)

        sdm = ShopDrawingModel.from_dict(canonical_json)
        self.assertEqual(sdm.backmark, "44")
        self.assertEqual(len(sdm.holes), 1, "ShopDrawingModel.from_dict must reconstruct holes from generator output")
        self.assertEqual(sdm.holes[0].along_mm, 35.0)
        self.assertEqual(sdm.title_block.standard, "IS_802")
        self.assertEqual(sdm.title_block.quantity, 4)
        self.assertEqual(len(sdm.dimension_chains), 2)

    def test_classify_section_ht_angle_and_edge_cases(self):
        """Verify classify_section handles 3-dimension high-tensile angles and various section forms correctly."""
        # 3 dimensions with HT -> HT_ANGLE, not HT_FLAT!
        self.assertEqual(classify_section("HT 50x50x5"), SectionType.HT_ANGLE)
        self.assertEqual(classify_section("HT 65X65X6"), SectionType.HT_ANGLE)
        self.assertEqual(classify_section("50x50x5"), SectionType.ANGLE)
        self.assertEqual(classify_section("HT 8 thk x 154"), SectionType.HT_FLAT)
        self.assertEqual(classify_section("PL 12"), SectionType.PLATE)
        self.assertEqual(classify_section(""), SectionType.UNKNOWN)

    def test_dimension_chain_non_positive_hole_coordinates_rejected(self):
        """Verify build_dimension_chains rejects non-positive hole coordinates and non-positive member lengths."""
        # Hole at 0.0 mm
        with self.assertRaises(ValueError):
            build_dimension_chains(1000.0, [0.0])

        # Negative hole coordinate
        with self.assertRaises(ValueError):
            build_dimension_chains(1000.0, [-15.0, 50.0])

        # Zero or negative member length
        with self.assertRaises(ValueError):
            build_dimension_chains(0.0, [50.0])
        with self.assertRaises(ValueError):
            build_dimension_chains(-500.0, [50.0])

    def test_draw_dimension_chain_requires_overall_length(self):
        """Verify _draw_dimension_chain strictly requires overall_length_mm and rejects None (no max(positions) fallback)."""
        doc = ezdxf.new()
        msp = doc.modelspace()
        with self.assertRaises(ValueError):
            _draw_dimension_chain(msp, [35.0, 75.0], 140, 80, scale=1.0, overall_length_mm=None)

    def test_validate_design_catches_zero_and_negative_hole_coordinates_and_duplicates(self):
        """Verify validate() detects non-positive hole positions, empty member lists, and duplicate backmarks."""
        # Hole at coordinate 0
        zero_hole = {
            "members": [{
                "backmark": "10",
                "section": "L50x50x5",
                "length_mm": 500,
                "qty": 2,
                "ends": {"E1": {"holes": [{"along_mm": 0.0, "hole_diameter_mm": 13.5}]}}
            }]
        }
        res = validate(zero_hole)
        self.assertFalse(res["valid"])
        self.assertTrue(any("strictly positive" in e for e in res["errors"]))

        # Pattern with start_from_end_mm = 0
        zero_pattern = {
            "members": [{
                "backmark": "10",
                "section": "L50x50x5",
                "length_mm": 500,
                "qty": 2,
                "ends": {"E1": {"pattern": {"count": 2, "start_from_end_mm": 0, "pitch_mm": 40, "hole_diameter_mm": 13.5}}}
            }]
        }
        res_pat = validate(zero_pattern)
        self.assertFalse(res_pat["valid"])
        self.assertTrue(any("must be positive" in e for e in res_pat["errors"]))

        # Empty members list
        empty_members = {"members": []}
        res_empty = validate(empty_members)
        self.assertFalse(res_empty["valid"])
        self.assertTrue(any("empty" in e for e in res_empty["errors"]))

        # Duplicate backmarks
        duplicate_marks = {
            "members": [
                {
                    "backmark": "44",
                    "section": "FLAT 4x45",
                    "length_mm": 1128,
                    "qty": 2,
                    "ends": {"E1": {"holes": [{"along_mm": 35, "hole_diameter_mm": 13.5}]}}
                },
                {
                    "backmark": "44",
                    "section": "FLAT 4x45",
                    "length_mm": 1128,
                    "qty": 2,
                    "ends": {"E1": {"holes": [{"along_mm": 35, "hole_diameter_mm": 13.5}]}}
                }
            ]
        }
        res_dup = validate(duplicate_marks)
        self.assertFalse(res_dup["valid"])
        self.assertTrue(any("Duplicate member backmark" in e for e in res_dup["errors"]))

    def test_validate_member_design_rejects_empty_hole_schedule(self):
        """Verify validate_member_design raises ValueError with MISSING_FABRICATION_HOLES when holes are empty."""
        member = {
            "backmark": "30",
            "section": "4 thk x94",
            "length_mm": 234.0,
            "qty": 2,
            "ends": {"E1": {"holes": []}, "E2": {"holes": []}}
        }
        rules = {"standard": "IS_802"}
        with self.assertRaises(ValueError) as ctx:
            validate_member_design(member, rules)
        self.assertIn("MISSING_FABRICATION_HOLES", str(ctx.exception))

    def test_inventory_from_schedule_with_domain_objects_and_list(self):
        """Verify inventory_from_schedule accepts a list of Member domain models directly."""
        hole = Hole(hole_id="H1", member_backmark="44", end="E1", along_mm=35.0, diameter_mm=13.5)
        end = MemberEnd(end_id="E1", holes=[hole])
        member = Member(
            backmark="44",
            section="FLAT 4x45",
            section_type=SectionType.FLAT,
            length_mm=1128.0,
            quantity=4,
            ends={"E1": end},
        )
        schedule = {"44": {"section": "FLAT 4x45", "length_mm": 1128.0}}
        # Pass list of Member domain models directly
        inv = inventory_from_schedule(schedule, [member])
        self.assertEqual(inv["currently_buildable_with_design_input"], 1)
        self.assertEqual(inv["blocked_count"], 0)
        self.assertIn("44", inv["buildable_backmarks"])

    def test_write_bom_with_dict_and_list_inputs(self):
        """Verify write_bom accepts dict and list structures directly."""
        hole = Hole(hole_id="H1", member_backmark="44", end="E1", along_mm=35.0, diameter_mm=13.5, nominal_bolt_diameter_mm=12.0)
        end = MemberEnd(end_id="E1", holes=[hole])
        member = Member(
            backmark="44",
            section="FLAT 4x45",
            section_type=SectionType.FLAT,
            length_mm=1128.0,
            quantity=4,
            ends={"E1": end},
        )
        with tempfile.TemporaryDirectory() as td:
            rows = write_bom([member], td)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["backmark"], "44")
            self.assertEqual(rows[0]["member_qty"], 4)
            self.assertEqual(rows[0]["total_qty"], 4)
            self.assertTrue((Path(td) / "job_bom.csv").exists())
            self.assertTrue((Path(td) / "job_bom.json").exists())


if __name__ == "__main__":
    unittest.main()

