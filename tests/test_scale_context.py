"""Unit tests for ScaleContext and LayerScanner."""
import unittest
from pathlib import Path
from scale_context import ScaleContext
from layer_scanner import classify_layers, scan_dxf_layers
import ezdxf


class TestScaleContextAndLayerScanner(unittest.TestCase):
    def setUp(self):
        self.dxf1_path = Path("loader") / "WO_429_LLA1_110kv_NT_PART P1 TO P3_ST No. 1 OF 6.dxf"
        self.dxf2_path = Path("C:/Users/omkar/Desktop/cghs/348_AD_PART- M2.dxf")

    def test_scale_context_dxf1(self):
        if not self.dxf1_path.exists():
            self.skipTest("DXF 1 not found")
        ctx = ScaleContext.from_dxf(self.dxf1_path)
        self.assertAlmostEqual(ctx.scale_factor, 1.0, delta=0.15)
        self.assertGreater(ctx.width_mm, 10000.0)
        self.assertGreater(ctx.height_mm, 8000.0)
        # Margin for 1800mm member
        margin = ctx.adaptive_locator_margin(1849.0)
        self.assertGreaterEqual(margin, 600.0)
        self.assertLessEqual(margin, 1000.0)

    def test_scale_context_dxf2(self):
        if not self.dxf2_path.exists():
            self.skipTest("DXF 2 not found")
        ctx = ScaleContext.from_dxf(self.dxf2_path)
        # DXF 2 diagonal is ~41,000 mm -> scale factor should be ~2.4 - 2.8
        self.assertGreater(ctx.scale_factor, 2.0)
        self.assertLess(ctx.scale_factor, 3.5)
        # For a 6115mm member, margin should be scaled adaptively
        margin = ctx.adaptive_locator_margin(6115.0)
        self.assertGreater(margin, 1500.0)
        self.assertLessEqual(margin, 4000.0)

    def test_layer_scanner_dxf1(self):
        dxf1_layers = [
            '0', '1_Point', '23_Member designation', '3_Members', '5_Bolts',
            '99_Frame', 'Arrows', 'BN1', 'BN2', 'Backmark 1', 'DIMENSION'
        ]
        res = classify_layers(dxf1_layers)
        self.assertEqual(res.primary_designation_layer, "23_Member designation")
        self.assertEqual(res.primary_member_layer, "3_Members")
        self.assertEqual(res.primary_bolt_layer, "5_Bolts")
        self.assertTrue(res.is_role("23_Member designation", "DESIGNATION"))
        self.assertTrue(res.is_role("3_Members", "MEMBERS"))
        self.assertTrue(res.is_role("5_Bolts", "BOLTS"))

    def test_layer_scanner_dxf2(self):
        dxf2_layers = [
            '0', '23_MEMBER_DESIGNATION', '3_MEMBERS', '5_BOLTS', 'BM',
            'BN2', 'BN4', 'BNT', 'DIMENSION', 'L10', 'LEG', 'PART', 'PLT'
        ]
        res = classify_layers(dxf2_layers)
        self.assertIn("23_MEMBER_DESIGNATION", res.designation_layers)
        self.assertIn("3_MEMBERS", res.member_layers)
        self.assertIn("L10", res.member_layers)
        self.assertIn("5_BOLTS", res.bolt_layers)
        self.assertTrue(res.is_role("23_MEMBER_DESIGNATION", "DESIGNATION"))
        self.assertTrue(res.is_role("3_MEMBERS", "MEMBERS"))
        self.assertTrue(res.is_role("L10", "MEMBERS"))


if __name__ == "__main__":
    unittest.main()
