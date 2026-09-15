"""Job-level BOM aggregation from approved per-member design input."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from domain.models import Member


def build_bom(design: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    """Derive canonical BOM rows strictly from validated domain models / design input.

    If quantity is missing or None, quantity status is marked as 'MISSING'
    and member_qty / total_qty are set to None (never defaulting to 1 or 2).
    """
    if isinstance(design, dict):
        members_data = design.get("members", [])
    elif isinstance(design, list):
        members_data = design
    else:
        members_data = []

    rows: list[dict[str, Any]] = []

    for item in members_data:
        # Canonical domain model instantiation
        member: Member | None = None
        if isinstance(item, Member):
            member = item
        elif isinstance(item, dict):
            try:
                member = Member.from_dict(item)
            except Exception:
                member = None

        if member is not None:
            mark = member.backmark
            section = member.section
            length_mm = member.length_mm
            raw_qty = member.quantity
        else:
            mark = item.get("backmark", "") if isinstance(item, dict) else ""
            section = item.get("section", "") if isinstance(item, dict) else ""
            length_mm = item.get("length_mm", 0.0) if isinstance(item, dict) else 0.0
            raw_qty = item.get("qty") if isinstance(item, dict) else None
            if raw_qty is None and isinstance(item, dict):
                raw_qty = item.get("quantity")

        # Validate quantity: never default to 1 or 2!
        qty = None
        qty_status = "VALID"
        if raw_qty is None or str(raw_qty).strip() == "":
            qty_status = "MISSING"
            qty = None
        else:
            try:
                parsed_qty = int(raw_qty)
                if parsed_qty <= 0:
                    qty_status = "INVALID"
                    qty = None
                else:
                    qty = parsed_qty
            except (TypeError, ValueError):
                qty_status = "INVALID"
                qty = None

        # Derive bolt / hole counts per piece
        per: Counter[str] = Counter()
        if member is not None:
            for end in member.ends.values():
                for h in end.holes:
                    if h.nominal_bolt_diameter_mm:
                        key = f"M{int(h.nominal_bolt_diameter_mm)}"
                    elif h.bolt_size:
                        key = h.bolt_size if str(h.bolt_size).startswith("M") else f"M{h.bolt_size}"
                    elif h.diameter_mm:
                        key = f"HOLE-{float(h.diameter_mm):g}"
                    else:
                        key = "HOLE-UNKNOWN"
                    per[key] += 1
        elif isinstance(item, dict):
            for end in item.get("ends", {}).values():
                if not isinstance(end, dict):
                    continue
                if end.get("pattern"):
                    p = end["pattern"]
                    dia = p.get("nominal_bolt_diameter_mm")
                    cnt = int(p.get("count", 0) or 0)
                    key = f"M{int(float(dia))}" if dia else f"HOLE-{float(p.get('hole_diameter_mm', 0)):g}"
                    per[key] += cnt
                else:
                    for h in end.get("holes", []):
                        if isinstance(h, dict):
                            dia = h.get("nominal_bolt_diameter_mm")
                            key = f"M{int(float(dia))}" if dia else f"HOLE-{float(h.get('hole_diameter_mm', 0)):g}"
                            per[key] += 1

        if not per:
            rows.append({
                "backmark": mark,
                "section": section,
                "length_mm": length_mm,
                "member_qty": qty,
                "quantity_status": qty_status,
                "item": "NO_HOLES",
                "qty_per_piece": 0,
                "total_qty": 0 if qty is not None else None,
            })
        else:
            for k in sorted(per.keys()):
                n = per[k]
                rows.append({
                    "backmark": mark,
                    "section": section,
                    "length_mm": length_mm,
                    "member_qty": qty,
                    "quantity_status": qty_status,
                    "item": k,
                    "qty_per_piece": n,
                    "total_qty": (n * qty) if qty is not None else None,
                })

    return rows


def write_bom(design_path: str | Path | dict[str, Any] | list[Any], out_dir: str | Path) -> list[dict[str, Any]]:
    if isinstance(design_path, (dict, list)):
        design = design_path
    else:
        p = Path(design_path)
        design = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    rows = build_bom(design)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fields = ["backmark", "section", "length_mm", "member_qty", "quantity_status", "item", "qty_per_piece", "total_qty"]
    with open(out / "job_bom.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    (out / "job_bom.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return rows
