"""Validate an approved fabrication-design JSON before generation against domain models and design rules."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from design_rules import load_rules, validate_pattern
from domain.models import Member, classify_section


def validate(design_input: str | Path | dict[str, Any], rules_input: str | Path | dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate design input JSON against domain models and engineering design rules."""
    if isinstance(design_input, dict):
        d = design_input
    else:
        p = Path(design_input)
        if not p.exists():
            return {"valid": False, "errors": [f"Design input file not found: {p}"], "error_count": 1}
        d = json.loads(p.read_text(encoding="utf-8"))

    rules = None
    if rules_input:
        if isinstance(rules_input, dict):
            rules = rules_input
        else:
            rp = Path(rules_input)
            if rp.exists():
                rules = load_rules(str(rp))

    errors: list[str] = []

    if not isinstance(d, dict):
        return {"valid": False, "errors": ["Top-level design input must be a JSON object"], "error_count": 1}

    members = d.get("members")
    if not isinstance(members, list):
        return {"valid": False, "errors": ["Design input must contain a 'members' list"], "error_count": 1}
    if not members:
        return {"valid": False, "errors": ["Design input 'members' list is empty"], "error_count": 1, "validated_members": 0}

    seen_marks: set[str] = set()
    for idx, m in enumerate(members):
        if not isinstance(m, dict):
            errors.append(f"Member at index {idx} must be a dictionary")
            continue

        mark = str(m.get("backmark", "") or "").strip()
        if not mark:
            errors.append(f"Member at index {idx} missing 'backmark'")
            mark = f"INDEX_{idx}"
        elif mark in seen_marks:
            errors.append(f"Duplicate member backmark '{mark}' found in design input")
        else:
            seen_marks.add(mark)

        # Section validation
        section = str(m.get("section", "") or "").strip()
        if not section:
            errors.append(f"{mark}: missing 'section'")

        # Length validation
        length_val = m.get("length_mm")
        length = 0.0
        try:
            length = float(length_val)
            if length <= 0:
                errors.append(f"{mark}: length_mm must be positive, got {length_val}")
        except (TypeError, ValueError):
            errors.append(f"{mark}: invalid length_mm: {length_val}")

        # Quantity validation: must be positive integer and not None
        raw_qty = m.get("qty")
        if raw_qty is None:
            raw_qty = m.get("quantity")
        if raw_qty is None:
            errors.append(f"{mark}: quantity is missing (MISSING_QTY)")
        else:
            try:
                qty = int(raw_qty)
                if qty <= 0:
                    errors.append(f"{mark}: quantity must be positive integer, got {raw_qty}")
            except (TypeError, ValueError):
                errors.append(f"{mark}: invalid quantity: {raw_qty}")

        # Domain model instantiation
        try:
            Member.from_dict(m)
        except Exception as exc:
            errors.append(f"{mark}: canonical domain model construction failed: {exc}")

        # Ends and hole validation
        ends = m.get("ends") or m.get("connection_ends") or {}
        if not isinstance(ends, dict):
            errors.append(f"{mark}: 'ends' must be a dictionary")
            continue

        total_holes = 0
        for end_name, end in ends.items():
            if not isinstance(end, dict):
                errors.append(f"{mark} {end_name}: end definition must be a dictionary")
                continue

            holes = []
            if end.get("pattern"):
                p = end["pattern"]
                try:
                    cnt = int(p.get("count", 0))
                    start = float(p.get("start_from_end_mm", 0))
                    pitch = float(p.get("pitch_mm", 0))
                    dia = float(p.get("hole_diameter_mm", 0))
                    edge = float(p.get("edge_distance_mm", 0))
                    if cnt <= 0:
                        errors.append(f"{mark} {end_name}: pattern count must be positive")
                    if dia <= 0:
                        errors.append(f"{mark} {end_name}: hole diameter must be positive")
                    if start <= 0:
                        errors.append(f"{mark} {end_name}: pattern start_from_end_mm ({start:g} mm) must be positive")
                    if pitch <= 0:
                        errors.append(f"{mark} {end_name}: pattern pitch_mm ({pitch:g} mm) must be positive")
                    for i in range(cnt):
                        pos = start + i * pitch
                        if pos <= 0:
                            errors.append(f"{mark} {end_name}: hole at {pos:g} mm must be strictly positive (0 < pos < {length:g} mm)")
                        elif length > 0 and pos >= length:
                            errors.append(f"{mark} {end_name}: hole at {pos:g} mm exceeds or equals member length {length:g} mm")
                        holes.append({"along_mm": pos, "hole_diameter_mm": dia, "edge_distance_mm": edge})
                except (TypeError, ValueError) as exc:
                    errors.append(f"{mark} {end_name}: invalid pattern definition: {exc}")
            elif "holes" in end:
                hl = end.get("holes")
                if not isinstance(hl, list):
                    errors.append(f"{mark} {end_name}: 'holes' must be a list")
                else:
                    for hi, h in enumerate(hl):
                        if not isinstance(h, dict):
                            errors.append(f"{mark} {end_name} hole[{hi}]: hole must be a dictionary")
                            continue
                        try:
                            pos = float(h.get("along_mm", 0))
                            dia = float(h.get("hole_diameter_mm", h.get("diameter_mm", 0)))
                            edge = float(h.get("edge_distance_mm", 0))
                            if dia <= 0:
                                errors.append(f"{mark} {end_name} hole[{hi}]: hole diameter must be positive")
                            if pos <= 0:
                                errors.append(f"{mark} {end_name} hole[{hi}]: hole at {pos:g} mm must be strictly positive (0 < pos < {length:g} mm)")
                            elif length > 0 and pos >= length:
                                errors.append(f"{mark} {end_name} hole[{hi}]: hole at {pos:g} mm exceeds or equals member length {length:g} mm")
                            holes.append({"along_mm": pos, "hole_diameter_mm": dia, "edge_distance_mm": edge})
                        except (TypeError, ValueError) as exc:
                            errors.append(f"{mark} {end_name} hole[{hi}]: invalid hole definition: {exc}")

            total_holes += len(holes)

            # Detailing rules validation
            if rules and holes and length > 0:
                r = validate_pattern(rules, length, holes)
                errors += [f"{mark} {end_name}: {e}" for e in r.get("errors", [])]

        if total_holes == 0:
            errors.append(f"{mark}: no fabrication holes defined (MISSING_FABRICATION_HOLES)")

    return {
        "valid": not errors,
        "errors": errors,
        "error_count": len(errors),
        "validated_members": len(members),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate engineering design input JSON")
    parser.add_argument("--engineering-input", "--design-input", dest="design_input", required=True,
                        help="Approved per-member fabrication design JSON")
    parser.add_argument("--rules", default=None, help="Approved project detailing rules JSON")
    args = parser.parse_args()
    res = validate(args.design_input, args.rules)
    print(json.dumps(res, indent=2))
    raise SystemExit(0 if res["valid"] else 2)
