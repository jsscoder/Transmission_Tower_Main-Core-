"""
Tests for Milestone 2: CAD Detailing & Rendering Engine.

Covers:
- Phase 5: Standard IS 802 Angle Gauge distance lookup
- Phases 3 & 7: Geometry & Section Profile Engine (AngleMemberRenderer, FlatMemberRenderer, EndSectionRenderer)
- Phase 6: Multi-Tier Dimension Engine (Level 1, Level 2, Level 3, irregular pitches)
- Phase 8: Sheet Layout, Auto-Scale Engine, Title Block & Hole Schedule Tables
- Phase 9: Native DXF + Headless PDF Export + Canonical JSON Sidecar
- Diagnostic members 37, 44, 45, 46 full fabrication packages
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import ezdxf

from design_rules import (
    load_rules,
    get_gauge_distance,
    get_gauge_distances,
    validate_pattern,
    DesignRuleError,
    IS_802_ANGLE_GAUGES,
)
from domain.models import (
    build_dimension_chains,
    Hole,
    Member,
    ShopDrawingModel,
    TitleBlockData,
)
from shop_drawing import (
    AngleMemberRenderer,
    FlatMemberRenderer,
    EndSectionRenderer,
    MultiTierDimensionRenderer,
    TitleBlockRenderer,
    HoleScheduleRenderer,
    select_drawing_scale,
    export_to_pdf,
    generate_member_dxf,
    generate_job,
    _parse_section,
    LAYER_MEMBER,
    LAYER_HOLE,
    LAYER_CENTER,
    LAYER_DIM,
    LAYER_TEXT,
    LAYER_SECTION,
    LAYER_BORDER,
    LAYER_TITLE,
    LAYER_TABLE,
)

ROOT = Path(__file__).resolve().parents[1]


class DetailingEngineTests(unittest.TestCase):

    def setUp(self):
        self.rules_path = ROOT / "design_rules.json"
        self.rules = load_rules(self.rules_path)
        self.conn_path = ROOT / "connection_design.json"
        self.conn_design = json.loads(self.conn_path.read_text(encoding="utf-8"))

    # ========================================================
    # PHASE 5: GAUGE DISTANCE LOOKUP
    # ========================================================

    def test_gauge_distance_all_standard_is802_widths(self):
        expected_table = {
            40: 22.0,
            45: 25.0,
            50: 28.0,
            55: 30.0,
            60: 35.0,
            65: 35.0,
            75: 40.0,
            90: 50.0,
            100: 55.0,
            110: 60.0,
            130: 70.0,
            150: 55.0,
        }
        for leg_w, expected_g in expected_table.items():
            # From raw integer/float
            self.assertEqual(get_gauge_distance(leg_w, self.rules), expected_g)
            # From section string
            sec_str = f"L{leg_w}x{leg_w}x6"
            self.assertEqual(get_gauge_distance(sec_str, self.rules), expected_g)

    def test_gauge_distance_string_variations_and_flats(self):
        self.assertEqual(get_gauge_distance("HTL45x45x5", self.rules), 25.0)
        self.assertEqual(get_gauge_distance("ISA 50x50x5", self.rules), 28.0)
        self.assertEqual(get_gauge_distance("L 90 x 90 x 6", self.rules), 50.0)
        self.assertEqual(get_gauge_distance("50x50x5", self.rules), 28.0)

        # Flat sections return centerline width / 2
        self.assertEqual(get_gauge_distance("FLAT 4x45", self.rules), 22.5)
        self.assertEqual(get_gauge_distance("4 thk x 45", self.rules), 22.5)

    def test_gauge_distance_custom_rules_override_and_unknown(self):
        custom_rules = {
            "standard": "CUSTOM_TOWER",
            "angle_gauge_rules": {
                "50": 32.0,
            }
        }
        # Custom rule should take precedence
        self.assertEqual(get_gauge_distance("L50x50x5", custom_rules), 32.0)
        # Non-overridden fallback
        self.assertEqual(get_gauge_distance("L45x45x5", custom_rules), 25.0)

        # Unknown leg width raises DesignRuleError
        with self.assertRaises(DesignRuleError):
            get_gauge_distance("L350x350x20", self.rules)

    # ========================================================
    # PHASE 8: AUTO-SCALE ENGINE
    # ========================================================

    def test_auto_scale_engine(self):
        # Short part: fits 1:1
        f, lbl = select_drawing_scale(200.0, max_width_mm=330.0)
        self.assertEqual((f, lbl), (1.0, "1:1"))

        # Medium part (600 mm): fits 1:2
        f, lbl = select_drawing_scale(600.0, max_width_mm=330.0)
        self.assertEqual((f, lbl), (0.5, "1:2"))

        # Part 44 (1128 mm): fits 1:5
        f, lbl = select_drawing_scale(1128.0, max_width_mm=330.0)
        self.assertEqual((f, lbl), (0.2, "1:5"))

        # Part 37 (2294 mm): fits 1:10
        f, lbl = select_drawing_scale(2294.0, max_width_mm=330.0)
        self.assertEqual((f, lbl), (0.1, "1:10"))

        # Long part (4500 mm): fits 1:15 or 1:20
        f, lbl = select_drawing_scale(4500.0, max_width_mm=330.0)
        self.assertIn(lbl, ["1:15", "1:20"])
        self.assertLessEqual(4500.0 * f, 330.0)

        # Very long part (6000 mm): fits 1:20 or 1:25
        f, lbl = select_drawing_scale(6000.0, max_width_mm=330.0)
        self.assertEqual(lbl, "1:20")
        self.assertLessEqual(6000.0 * f, 330.0)

    # ========================================================
    # PHASES 3 & 7: SECTION PROFILE & RENDERERS
    # ========================================================

    def test_angle_member_renderer_profile(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        renderer = AngleMemberRenderer(leg_a_mm=50, leg_b_mm=50, thickness_mm=5, length_mm=2294, gauge_mm=28.0)
        holes = [
            {"along_mm": 35.0, "hole_diameter_mm": 11.5, "end": "E1"},
            {"along_mm": 70.0, "hole_diameter_mm": 11.5, "end": "E1"},
        ]
        info = renderer.render(msp, origin=(50.0, 200.0), holes=holes, scale=0.1)

        self.assertEqual(info["kind"], "ANGLE")
        self.assertAlmostEqual(info["scaled_length"], 229.4)
        self.assertAlmostEqual(info["scaled_width"], 5.0)

        # Check entities on respective layers
        member_lines = [e for e in msp.query("LINE") if e.dxf.layer == LAYER_MEMBER]
        # Heel, toe, left cut, right cut, thickness line -> at least 5 lines
        self.assertGreaterEqual(len(member_lines), 5)

        hole_circles = [e for e in msp.query("CIRCLE") if e.dxf.layer == LAYER_HOLE]
        self.assertEqual(len(hole_circles), 2)

        # Check center lines (gauge line + hole crosses)
        center_lines = [e for e in msp.query("LINE") if e.dxf.layer == LAYER_CENTER]
        self.assertGreaterEqual(len(center_lines), 5)

    def test_flat_member_renderer_profile(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        renderer = FlatMemberRenderer(width_mm=45, thickness_mm=4, length_mm=1128)
        holes = [
            {"along_mm": 35.0, "hole_diameter_mm": 13.5, "end": "E1"},
        ]
        info = renderer.render(msp, origin=(50.0, 200.0), holes=holes, scale=0.2)

        self.assertEqual(info["kind"], "FLAT")
        self.assertAlmostEqual(info["scaled_length"], 225.6)
        self.assertAlmostEqual(info["scaled_width"], 9.0)

        member_lines = [e for e in msp.query("LINE") if e.dxf.layer == LAYER_MEMBER]
        # Closed rectangular boundary = 4 lines
        self.assertEqual(len(member_lines), 4)

        hole_circles = [e for e in msp.query("CIRCLE") if e.dxf.layer == LAYER_HOLE]
        self.assertEqual(len(hole_circles), 1)

    def test_end_section_renderer(self):
        # Angle end section (L-shape closed polygon = 6 segments)
        doc = ezdxf.new()
        msp = doc.modelspace()
        end_angle = EndSectionRenderer({"kind": "ANGLE", "leg_a_mm": 50, "leg_b_mm": 50, "thickness_mm": 5})
        end_angle.render(msp, origin=(40.0, 50.0), scale=1.0)
        sec_lines_angle = [e for e in msp.query("LINE") if e.dxf.layer == LAYER_SECTION]
        self.assertEqual(len(sec_lines_angle), 6, "Angle end section must be an L-shaped 6-segment closed polygon")

        # Flat end section (rectangle = 4 segments)
        doc2 = ezdxf.new()
        msp2 = doc2.modelspace()
        end_flat = EndSectionRenderer({"kind": "FLAT", "width_mm": 45, "thickness_mm": 4})
        end_flat.render(msp2, origin=(40.0, 50.0), scale=1.0)
        sec_lines_flat = [e for e in msp2.query("LINE") if e.dxf.layer == LAYER_SECTION]
        self.assertEqual(len(sec_lines_flat), 4, "Flat end section must be a 4-segment rectangle")

    # ========================================================
    # PHASE 6: MULTI-TIER DIMENSION ENGINE
    # ========================================================

    def test_irregular_hole_pitch_sequence_dimensions(self):
        # Diagnostic member 44 irregular pitch sequence:
        # intervals: 22, 204, 185, 65, 185, 65, 185, 195, 22 -> total 1128
        cumulative_positions = [22, 226, 411, 476, 661, 726, 911, 1106]
        holes = [{"along_mm": pos, "hole_diameter_mm": 13.5} for pos in cumulative_positions]

        chains = build_dimension_chains(1128.0, holes, gauge_mm=22.5)
        self.assertEqual(len(chains), 3, "Must produce Level 1, Level 2, and Level 3 chains when gauge is provided")

        level1 = chains[0]
        expected_intervals = [22.0, 204.0, 185.0, 65.0, 185.0, 65.0, 185.0, 195.0, 22.0]
        self.assertEqual(len(level1), len(expected_intervals))
        for item, exp in zip(level1, expected_intervals):
            self.assertAlmostEqual(item.value_mm, exp)

        level2 = chains[1]
        self.assertEqual(len(level2), 1)
        self.assertEqual(level2[0].value_mm, 1128.0)
        self.assertEqual(level2[0].kind, "overall")

        level3 = chains[2]
        self.assertEqual(len(level3), 1)
        self.assertEqual(level3[0].value_mm, 22.5)
        self.assertEqual(level3[0].kind, "gauge")

    def test_multi_tier_dimension_renderer_cad_offsets(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        holes = [
            {"along_mm": 35.0, "hole_diameter_mm": 11.5},
            {"along_mm": 70.0, "hole_diameter_mm": 11.5},
        ]
        renderer = MultiTierDimensionRenderer(length_mm=1000.0, holes=holes, gauge_mm=28.0)
        renderer.render(
            msp,
            origin=(45.0, 200.0),
            scale=0.1,
            dim_y_level1=175.0,
            dim_y_level2=158.0,
            member_bottom_y=200.0,
        )

        dim_lines = [e for e in msp.query("LINE") if e.dxf.layer == LAYER_DIM]
        self.assertGreater(len(dim_lines), 5)

        # Check text labels on dim layer
        dim_texts = [e.dxf.text for e in msp.query("TEXT") if e.dxf.layer == LAYER_DIM]
        self.assertTrue(any("OVERALL = 1000 mm" in t for t in dim_texts))
        self.assertTrue(any("GAUGE = 28 mm" in t for t in dim_texts))

    # ========================================================
    # PHASE 8: TITLE BLOCK & HOLE SCHEDULE TABLE
    # ========================================================

    def test_title_block_renderer_fields(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        tb_data = {
            "drawing_id": "429B37",
            "backmark": "37",
            "section": "L50x50x5",
            "length_mm": 2294.0,
            "quantity": 2,
            "job": "WO_429",
            "standard": "IS 802",
            "status": "FABRICATION READY",
            "rev": "0",
            "date": "2026-09-15",
        }
        renderer = TitleBlockRenderer(tb_data)
        renderer.render(msp, origin=(245.0, 15.0), width=160.0, height=55.0)

        # Verify title lines
        title_lines = [e for e in msp.query("LINE") if e.dxf.layer == LAYER_TITLE]
        self.assertGreater(len(title_lines), 8)

        # Verify field contents in text entities
        texts = [e.dxf.text for e in msp.query("TEXT") if e.dxf.layer == LAYER_TEXT]
        self.assertIn("429B37", texts)
        self.assertIn("37", texts)
        self.assertIn("L50x50x5", texts)
        self.assertIn("2294 mm", texts)
        self.assertIn("WO_429", texts)
        self.assertIn("IS 802", texts)
        self.assertIn("FABRICATION READY", texts)

    def test_hole_schedule_renderer_columns(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        holes = [
            {"along_mm": 35.0, "hole_diameter_mm": 11.5, "nominal_bolt_diameter_mm": 10.0, "end": "E1"},
            {"along_mm": 70.0, "hole_diameter_mm": 11.5, "nominal_bolt_diameter_mm": 10.0, "end": "E1"},
            {"along_mm": 2259.0, "hole_diameter_mm": 13.5, "nominal_bolt_diameter_mm": 12.0, "end": "E2"},
        ]
        sched = HoleScheduleRenderer(holes, member_qty=2)
        count = sched.render(msp, origin=(245.0, 75.0), width=160.0)

        self.assertEqual(count, 2, "Must group into 2 distinct hole rows: Ø11.5/M10 and Ø13.5/M12")
        table_lines = [e for e in msp.query("LINE") if e.dxf.layer == LAYER_TABLE]
        self.assertGreater(len(table_lines), 6)

        texts = [e.dxf.text for e in msp.query("TEXT") if e.dxf.layer == LAYER_TEXT]
        self.assertIn("BOLT / HOLE SCHEDULE", texts)
        self.assertIn("Ø11.5", texts)
        self.assertIn("M10", texts)
        self.assertIn("Ø13.5", texts)
        self.assertIn("M12", texts)

    # ========================================================
    # PHASE 9 & DIAGNOSTIC MEMBERS: 37, 44, 45, 46 PACKAGES
    # ========================================================

    def test_generate_member_dxf_layers_and_pdf_export(self):
        member = next(m for m in self.conn_design["members"] if m["backmark"] == "44")
        with tempfile.TemporaryDirectory() as td:
            out_dxf = Path(td) / "429B44.dxf"
            res = generate_member_dxf(member, self.rules, out_dxf, export_pdf=True)

            self.assertTrue(out_dxf.exists())
            self.assertTrue(Path(res["json_path"]).exists())
            self.assertTrue(Path(res["pdf_path"]).exists())
            self.assertGreater(Path(res["pdf_path"]).stat().st_size, 1000)

            # Check DXF layers
            doc = ezdxf.readfile(out_dxf)
            layer_names = set(layer.dxf.name for layer in doc.layers)
            expected_layers = {
                LAYER_MEMBER,
                LAYER_HOLE,
                LAYER_CENTER,
                LAYER_DIM,
                LAYER_TEXT,
                LAYER_SECTION,
                LAYER_BORDER,
                LAYER_TITLE,
                LAYER_TABLE,
            }
            self.assertTrue(expected_layers.issubset(layer_names), f"Missing layers: {expected_layers - layer_names}")

            # Check JSON sidecar content
            sidecar = json.loads(Path(res["json_path"]).read_text(encoding="utf-8"))
            self.assertEqual(sidecar["drawing_id"], "429B44")
            self.assertEqual(sidecar["backmark"], "44")
            self.assertEqual(sidecar["qty"], 4)
            self.assertIn("per_piece", sidecar)
            self.assertIn("scale", sidecar)

    def test_diagnostic_members_37_44_45_46_full_packages(self):
        """Generate and verify full shop drawing packages for all diagnostic members."""
        diagnostic_marks = ["37", "44", "45", "46"]
        members_by_mark = {m["backmark"]: m for m in self.conn_design["members"] if m["backmark"] in diagnostic_marks}

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            results = {}
            for mark in diagnostic_marks:
                mem = members_by_mark[mark]
                dxf_file = out_dir / f"429B{mark}.dxf"
                res = generate_member_dxf(mem, self.rules, dxf_file, export_pdf=True)
                results[mark] = res

            # Verify Member 37 (Angle L50x50x5, length 2294, qty 2, 5xM10 + 1xM12)
            res37 = results["37"]
            self.assertEqual(res37["hole_count"], 6)
            self.assertEqual(res37["qty"], 2)
            self.assertTrue(Path(res37["dxf"]).exists())
            self.assertTrue(Path(res37["pdf"]).exists())
            self.assertTrue(Path(res37["json"]).exists())
            json37 = json.loads(Path(res37["json"]).read_text(encoding="utf-8"))
            self.assertEqual(json37["per_piece"]["M10"], 5)
            self.assertEqual(json37["per_piece"]["M12"], 1)

            # Verify Member 44 (Flat 4x45, length 1128, qty 4, 8xM12)
            res44 = results["44"]
            self.assertEqual(res44["hole_count"], 8)
            self.assertEqual(res44["qty"], 4)
            self.assertTrue(Path(res44["dxf"]).exists())
            self.assertTrue(Path(res44["pdf"]).exists())
            self.assertTrue(Path(res44["json"]).exists())
            json44 = json.loads(Path(res44["json"]).read_text(encoding="utf-8"))
            self.assertEqual(json44["per_piece"]["M12"], 8)

            # Verify Member 45 (Flat 4x45, length 1280, qty 2, 6xM12)
            res45 = results["45"]
            self.assertEqual(res45["hole_count"], 6)
            self.assertEqual(res45["qty"], 2)
            self.assertTrue(Path(res45["dxf"]).exists())
            self.assertTrue(Path(res45["pdf"]).exists())
            self.assertTrue(Path(res45["json"]).exists())
            json45 = json.loads(Path(res45["json"]).read_text(encoding="utf-8"))
            self.assertEqual(json45["per_piece"]["M12"], 6)

            # Verify Member 46 (Flat 4x45, length 664, qty 4, 4xM12)
            res46 = results["46"]
            self.assertEqual(res46["hole_count"], 4)
            self.assertEqual(res46["qty"], 4)
            self.assertTrue(Path(res46["dxf"]).exists())
            self.assertTrue(Path(res46["pdf"]).exists())
            self.assertTrue(Path(res46["json"]).exists())
            json46 = json.loads(Path(res46["json"]).read_text(encoding="utf-8"))
            self.assertEqual(json46["per_piece"]["M12"], 4)

    def test_export_pdf_false_option(self):
        member = next(m for m in self.conn_design["members"] if m["backmark"] == "46")
        with tempfile.TemporaryDirectory() as td:
            dxf_file = Path(td) / "429B46.dxf"
            res = generate_member_dxf(member, self.rules, dxf_file, export_pdf=False)
            self.assertTrue(dxf_file.exists())
            self.assertTrue(Path(res["json_path"]).exists())
            self.assertFalse(Path(res["pdf_path"]).exists())

    def test_export_to_pdf_custom_page_size_and_dpi(self):
        member = next(m for m in self.conn_design["members"] if m["backmark"] == "46")
        with tempfile.TemporaryDirectory() as td:
            dxf_file = Path(td) / "429B46.dxf"
            pdf_a4 = Path(td) / "429B46_a4.pdf"
            generate_member_dxf(member, self.rules, dxf_file, export_pdf=False)
            export_to_pdf(dxf_file, pdf_a4, page_size="A4", dpi=150)
            self.assertTrue(pdf_a4.exists())
            self.assertGreater(pdf_a4.stat().st_size, 500)

    def test_generate_job_manifest_and_pdf_export(self):
        diagnostic_marks = ["37", "44"]
        members_subset = [m for m in self.conn_design["members"] if m["backmark"] in diagnostic_marks]
        with tempfile.TemporaryDirectory() as td:
            design_file = Path(td) / "approved_design.json"
            design_file.write_text(json.dumps({"members": members_subset}, indent=2), encoding="utf-8")

            out_dir = Path(td) / "drawings"
            job_results = generate_job(design_file, self.rules_path, out_dir, export_pdf=True)
            self.assertEqual(len(job_results), 2)

            manifest_file = out_dir / "generation_manifest.json"
            self.assertTrue(manifest_file.exists())
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            self.assertEqual(manifest["drawing_count"], 2)
            self.assertTrue((out_dir / "429B37.pdf").exists())
            self.assertTrue((out_dir / "429B44.pdf").exists())

    def test_section_parser_all_angle_and_flat_formats(self):
        # HT angles (previously failed with UNKNOWN)
        p1 = _parse_section("HT 50x50x5")
        self.assertEqual(p1["kind"], "ANGLE")
        self.assertEqual((p1["leg_a_mm"], p1["leg_b_mm"], p1["thickness_mm"]), (50.0, 50.0, 5.0))

        p2 = _parse_section("HT50X50X5")
        self.assertEqual(p2["kind"], "ANGLE")
        self.assertEqual((p2["leg_a_mm"], p2["leg_b_mm"], p2["thickness_mm"]), (50.0, 50.0, 5.0))

        p3 = _parse_section("HTL45x45x5")
        self.assertEqual(p3["kind"], "ANGLE")
        self.assertEqual((p3["leg_a_mm"], p3["leg_b_mm"], p3["thickness_mm"]), (45.0, 45.0, 5.0))

        # Flange thickness with decimals
        p4 = _parse_section("L 45x45x4.5")
        self.assertEqual(p4["kind"], "ANGLE")
        self.assertEqual(p4["thickness_mm"], 4.5)

        # Flats with THK
        p5 = _parse_section("HT8 thk x154")
        self.assertEqual(p5["kind"], "FLAT")
        self.assertEqual((p5["thickness_mm"], p5["width_mm"]), (8.0, 154.0))

        p6 = _parse_section("6 thk x99")
        self.assertEqual(p6["kind"], "FLAT")
        self.assertEqual((p6["thickness_mm"], p6["width_mm"]), (6.0, 99.0))

        # Flats with PL or FLAT
        p7 = _parse_section("PL 8x150")
        self.assertEqual(p7["kind"], "FLAT")
        self.assertEqual((p7["thickness_mm"], p7["width_mm"]), (8.0, 150.0))

    def test_gauge_distance_150mm_double_gauge_line(self):
        # Default line 1 (55 mm)
        self.assertEqual(get_gauge_distance(150, self.rules), 55.0)
        self.assertEqual(get_gauge_distance(150, self.rules, gauge_line=1), 55.0)
        # Line 2 (95 mm)
        self.assertEqual(get_gauge_distance(150, self.rules, gauge_line=2), 95.0)
        # Both lines via get_gauge_distances
        self.assertEqual(get_gauge_distances(150, self.rules), (55.0, 95.0))

        # From section string HTL150x150x12
        self.assertEqual(get_gauge_distance("HTL150x150x12", self.rules, gauge_line=1), 55.0)
        self.assertEqual(get_gauge_distance("HTL150x150x12", self.rules, gauge_line=2), 95.0)
        self.assertEqual(get_gauge_distances("HTL150x150x12", self.rules), (55.0, 95.0))

        # Single gauge sections return tuple of length 1
        self.assertEqual(get_gauge_distances("L50x50x5", self.rules), (28.0,))

    def test_double_gauge_dimension_chain_and_rendering(self):
        holes = [
            {"along_mm": 50.0, "hole_diameter_mm": 13.5, "transverse_mm": 55.0},
            {"along_mm": 100.0, "hole_diameter_mm": 13.5, "transverse_mm": 95.0},
        ]
        # Multi-tier dimension chains with double gauge
        chains = build_dimension_chains(1000.0, holes, gauge_mm=(55.0, 95.0))
        self.assertEqual(len(chains), 3)
        level3 = chains[2]
        self.assertEqual(len(level3), 2)
        self.assertEqual(level3[0].value_mm, 55.0)
        self.assertEqual(level3[1].value_mm, 40.0)

        # Angle renderer with double gauge lines draws centerlines for both lines
        doc = ezdxf.new()
        msp = doc.modelspace()
        renderer = AngleMemberRenderer(150.0, 150.0, 12.0, 1000.0, gauge_mm=(55.0, 95.0))
        renderer.render(msp, origin=(55.0, 205.0), holes=holes, scale=0.1)

        # Both gauge centerlines plus hole center crosses
        center_lines = [e for e in msp.query("LINE") if e.dxf.layer == LAYER_CENTER]
        # 2 gauge longitudinal lines + 2 crosses (4 lines) = 6 center lines
        self.assertGreaterEqual(len(center_lines), 6)

    def test_auto_scale_considers_profile_height(self):
        # Short part with large height (length 200, height 150)
        # If height is ignored, it would pick 1:1, causing profile height 150mm to exceed the A3 frame
        scale_f, scale_lbl = select_drawing_scale(length_mm=200.0, max_width_mm=330.0, height_mm=150.0, max_height_mm=70.0)
        self.assertLessEqual(150.0 * scale_f, 70.0)
        self.assertIn(scale_lbl, ["1:5", "1:10"])

    def test_border_clearance_and_no_clipping(self):
        member = next(m for m in self.conn_design["members"] if m["backmark"] == "37")
        with tempfile.TemporaryDirectory() as td:
            out_dxf = Path(td) / "429B37.dxf"
            res = generate_member_dxf(member, self.rules, out_dxf, export_pdf=True)

            doc = ezdxf.readfile(out_dxf)
            msp = doc.modelspace()

            # Verify that dimension lines and text do not cross outside sheet margin
            margin = 10.0
            sheet_w = 420.0
            sheet_h = 297.0
            for ent in msp.query("TEXT"):
                if ent.dxf.layer in (LAYER_DIM, LAYER_TEXT):
                    x = ent.dxf.insert.x
                    y = ent.dxf.insert.y
                    self.assertGreaterEqual(x, margin, f"Text '{ent.dxf.text}' at x={x} is left of sheet margin {margin}")
                    self.assertLessEqual(x, sheet_w - margin, f"Text '{ent.dxf.text}' at x={x} is right of sheet margin")
                    self.assertGreaterEqual(y, margin, f"Text '{ent.dxf.text}' at y={y} is below sheet margin")
                    self.assertLessEqual(y, sheet_h - margin, f"Text '{ent.dxf.text}' at y={y} is above sheet margin")

    def test_canonical_shop_drawing_model_roundtrip(self):
        member = next(m for m in self.conn_design["members"] if m["backmark"] == "44")
        with tempfile.TemporaryDirectory() as td:
            out_dxf = Path(td) / "429B44.dxf"
            res = generate_member_dxf(member, self.rules, out_dxf, export_pdf=False)

            json_text = Path(res["json_path"]).read_text(encoding="utf-8")
            data = json.loads(json_text)

            # Canonical fields present in sidecar
            self.assertIn("member", data)
            self.assertIn("end_section", data)
            self.assertIn("title_block", data)
            self.assertIn("status", data)
            self.assertEqual(data["end_section"]["kind"], "FLAT")

            # Roundtrip to ShopDrawingModel domain object
            model = ShopDrawingModel.from_dict(data)
            self.assertEqual(model.drawing_id, "429B44")
            self.assertEqual(model.backmark, "44")
            self.assertEqual(model.end_section["kind"], "FLAT")
            self.assertEqual(model.title_block.standard, "IS_802_TRANSMISSION_TOWER")
            self.assertEqual(len(model.holes), 8)

    def test_staggered_hole_pitch_validation(self):
        # Two holes on different gauge lines (55 and 95) with along_mm delta = 15 (< min_pitch 27 for Ø13.5)
        holes = [
            {"along_mm": 50.0, "hole_diameter_mm": 13.5, "transverse_mm": 55.0, "edge_distance_mm": 25.0},
            {"along_mm": 65.0, "hole_diameter_mm": 13.5, "transverse_mm": 95.0, "edge_distance_mm": 25.0},
        ]
        # Should be valid because they are on separate transverse gauge lines
        res = validate_pattern(self.rules, 1000.0, holes)
        self.assertTrue(res["valid"], f"Staggered holes on separate gauge lines should pass: {res.get('errors')}")


if __name__ == "__main__":
    unittest.main()

