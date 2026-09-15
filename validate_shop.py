"""Compare assembly-DXF evidence against known resultant shop drawings.

The fixture is deliberately kept separate from extraction logic: it is a
validation target, not a source of inferred production data. Nothing in
here feeds back into extraction/resolution -- it only reports.

FIXED (previous version had two real bugs, confirmed by running it):
  1. It read e.get('bolt_evidence', []) and b['bolt_diameter_mm'], but
     shop_pipeline.extract_member_evidence() actually returns
     'assembly_circle_evidence' with keys 'circle_diameter_mm' and
     'mapped_nominal_diameter_mm'. The old keys never existed, so this
     script always silently printed an empty evidence count -- it looked
     like it ran successfully but never showed real data.
  2. DXF path was hardcoded to a relative path with a space in the
     filename that did not match the actual uploaded file. Now it's a
     --dxf argument like every other stage script.

Also extended to show the resolver's actual AUTO/REVIEW/REJECT verdict
for each fixture, since raw evidence counts alone don't tell you whether
the pipeline would have trusted its own answer.
"""
from __future__ import annotations
import argparse, json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import tower
from connection_annotations import extract_bolt_callouts, group_stacked_callouts, groups_as_dicts
from connection_association import associate
from shop_pipeline import extract_member_evidence
from connection_topology import build_topology
from connection_resolver import resolve


def main():
    ap = argparse.ArgumentParser(description="Validate pipeline output against known real shop drawings")
    ap.add_argument("--dxf", required=True, help="Path to the assembly .dxf")
    ap.add_argument(
        "--ground-truth",
        default=str(ROOT / "fixtures" / "shop_ground_truth.json"),
        help="JSON file of known correct shop-drawing bolt data (default: bundled fixture)",
    )
    args = ap.parse_args()

    dxf = args.dxf
    gt = json.loads(Path(args.ground_truth).read_text())

    schedule = tower.extract_member_schedule(dxf)
    evidence = extract_member_evidence(dxf, schedule)

    callouts = extract_bolt_callouts(dxf)
    groups = group_stacked_callouts(callouts)
    group_dicts = groups_as_dicts(groups)
    associations = associate(dxf)
    topology = build_topology(dxf, schedule, evidence, group_dicts)
    groups_by_id = {g["group_id"]: g for g in group_dicts}
    resolved = resolve(associations, topology, groups_by_id)
    resolved_by_mark = resolved["members"]

    print(f"{'shop id':10s} {'mark':5s} {'shop bolts/piece':22s} {'evidence circle diameters':28s} {'resolver verdict'}")
    print("-" * 100)
    for shop_id, g in sorted(gt.items()):
        mark = g["source_backmark"]
        e = evidence.get(mark, {})
        geom = e.get("geometry")

        if geom:
            circ = Counter(str(b["mapped_nominal_diameter_mm"]) for b in e.get("assembly_circle_evidence", []))
            evidence_str = dict(circ) if circ else "(no circles found near member)"
        else:
            evidence_str = "NO MEMBER GEOMETRY MATCHED"

        ends = resolved_by_mark.get(mark, {}).get("connection_ends", [])
        if ends:
            verdict = ", ".join(f"{e['end_id']}={e['status']}({e['confidence']:.2f})" for e in ends)
        else:
            verdict = "no connection candidates"

        shop_bolts = g["per_piece"]
        print(f"{shop_id:10s} {mark:5s} {str(shop_bolts):22s} {str(evidence_str):28s} {verdict}")

    print(
        "\nRead this as: 'shop bolts/piece' is the REAL answer from the scanned shop drawing. "
        "'evidence circle diameters' is what the pipeline found geometrically near that member in "
        "the assembly DXF -- it is frequently incomplete or from a neighboring connection, which is "
        "exactly why the resolver verdict is rarely AUTO. Treat AUTO as trustworthy, REVIEW as "
        "'a person should look at this', and REJECT as 'no reliable evidence found'."
    )


if __name__ == "__main__":
    main()
