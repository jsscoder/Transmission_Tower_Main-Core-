"""
GOLDEN GATE CLI — Run the 4-drawing regression evaluation.

Usage:
    python run_golden_gate.py [--fixtures FIXTURES_DIR] [--shop SHOP_DIR] [--out OUT_DIR]
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Ensure the deliverable directory is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from golden_score import run_golden_gate


def main():
    parser = argparse.ArgumentParser(description="Golden Gate 4-Drawing Regression Evaluation")
    parser.add_argument("--fixtures", default="fixtures", help="Path to fixtures directory")
    parser.add_argument("--shop", default="pipeline_out/shop_drawings", help="Path to generated shop drawings")
    parser.add_argument("--ref-pdf", default=None, help="Path to reference PDF directory (optional)")
    parser.add_argument("--out", default="pipeline_out", help="Output directory for reports")
    args = parser.parse_args()

    fixtures_dir = Path(args.fixtures)
    shop_dir = Path(args.shop)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("  GOLDEN GATE -- 4-Drawing Regression Evaluation")
    print("=" * 70)
    print(f"  Fixtures : {fixtures_dir.resolve()}")
    print(f"  Shop     : {shop_dir.resolve()}")
    print(f"  Output   : {out_dir.resolve()}")
    print("=" * 70)

    result = run_golden_gate(fixtures_dir, shop_dir, ref_pdf_dir=args.ref_pdf)

    # JSON report
    json_path = out_dir / "regression_4_drawings.json"
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"\n  JSON report: {json_path}")

    # CSV report
    csv_path = out_dir / "regression_4_drawings.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "drawing_id", "backmark",
            "A_metadata", "B_fabrication", "C_geometry",
            "D_dimension", "E_layout", "F_visual",
            "overall_score", "status",
            "provenance_issues_count", "missing_fields_count",
            "differences_count",
        ])
        for d in result["drawings"]:
            writer.writerow([
                d.get("drawing_id", ""),
                d.get("backmark", ""),
                d.get("metadata_score", ""),
                d.get("fabrication_score", ""),
                d.get("geometry_score", ""),
                d.get("dimension_score", ""),
                d.get("layout_score", ""),
                d.get("visual_score", ""),
                d.get("overall_score", ""),
                d.get("status", ""),
                len(d.get("provenance_issues", [])),
                len(d.get("missing_fields", [])),
                len(d.get("differences", [])),
            ])
    print(f"  CSV report: {csv_path}")

    # Console summary
    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)
    print(f"  {'Drawing':<12} {'Meta':>6} {'Fab':>6} {'Geom':>6} {'Dim':>6} {'Lay':>6} {'Vis':>6} {'TOTAL':>7}  {'STATUS'}")
    print("  " + "-" * 66)
    for d in result["drawings"]:
        status_icon = "[OK]" if d.get("status") == "PASS" else "[XX]"
        print(
            f"  {d.get('drawing_id',''):<12}"
            f" {d.get('metadata_score',0):>5.1f}"
            f" {d.get('fabrication_score',0):>5.1f}"
            f" {d.get('geometry_score',0):>5.1f}"
            f" {d.get('dimension_score',0):>5.1f}"
            f" {d.get('layout_score',0):>5.1f}"
            f" {d.get('visual_score',0):>5.1f}"
            f" {d.get('overall_score',0):>6.1f}%"
            f"  {status_icon} {d.get('status','')}"
        )

    print("  " + "-" * 66)
    gate = result.get("gate_status", "FAIL")
    gate_icon = "[OK]" if gate == "PASS" else "[XX]"
    print(f"  GOLDEN GATE: {gate_icon} {gate}")
    print("=" * 70)

    # Print differences for failing drawings
    for d in result["drawings"]:
        if d.get("status") != "PASS":
            diffs = d.get("differences", [])
            prov = d.get("provenance_issues", [])
            missing = d.get("missing_fields", [])
            if diffs or prov or missing:
                print(f"\n  {d['drawing_id']} differences:")
                for diff in diffs[:10]:
                    print(f"    - {diff}")
                if len(diffs) > 10:
                    print(f"    ... and {len(diffs) - 10} more")
                if prov:
                    print(f"    Provenance issues: {len(prov)}")
                if missing:
                    print(f"    Missing fields: {len(missing)}")

    return 0 if gate == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
