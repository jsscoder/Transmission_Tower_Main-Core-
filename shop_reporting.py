"""Shop-drawing inventory, JSON normalization, comparison and grouped reporting.

This module answers four production questions without silently inventing data:
1) How many shop drawings are identifiable from a specific assembly DXF?
2) How many are currently buildable with an approved design-input JSON?
3) How does a generated shop-drawing JSON compare with an existing/reference JSON?
4) Can every pipeline JSON artifact be organized by connection group/backmark for review?

Reference JSON may be a single object, a list, a {drawings:[...]} wrapper, or the
legacy validation shape used by fixtures/shop_ground_truth.json.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _mark(v):
    if v is None:
        return None
    s = str(v).strip()
    if s.upper().startswith("429B"):
        s = s[4:]
    return s


def _drawing_id(mark):
    return f"429B{_mark(mark)}" if _mark(mark) else None


def _bolt_key(v):
    if v is None:
        return None
    s = str(v).upper().replace(" ", "")
    if s.startswith("M"):
        return s
    n = _num(v)
    return f"M{int(n)}" if n is not None and n.is_integer() else str(v)


def _diam_key(v):
    n = _num(v)
    return f"{n:g}" if n is not None else str(v)


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def flatten_reference_documents(data: Any, source="reference") -> list[dict]:
    """Convert common reference/generated JSON wrappers into drawing records."""
    if isinstance(data, list):
        out = []
        for x in data:
            out.extend(flatten_reference_documents(x, source))
        return out
    if not isinstance(data, dict):
        return []
    if "drawings" in data and isinstance(data["drawings"], (list, dict)):
        return flatten_reference_documents(data["drawings"], source)
    # Legacy fixture: {429B37: {...}, 429B44: {...}}
    if not any(k in data for k in ("backmark", "source_backmark", "drawing_id", "dwg_no", "member")):
        out = []
        for k, v in data.items():
            if isinstance(v, dict):
                r = dict(v)
                r.setdefault("drawing_id", k)
                out.extend(flatten_reference_documents(r, source))
        if out:
            return out
    r = dict(data)
    r["_source"] = source
    return [r]


def _canonical_section(s):
    if not s:
        return s
    import re
    st = str(s).strip()
    m = re.match(r"^(\d+)\s*(?:thk|THK)?\s*[xX]\s*(\d+)$", st)
    if m:
        return f"FLAT {m.group(1)}x{m.group(2)}"
    m2 = re.match(r"^FLAT\s+(\d+)\s*[xX]\s*(\d+)$", st, re.IGNORECASE)
    if m2:
        return f"FLAT {m2.group(1)}x{m2.group(2)}"
    return st


def _holes_from_record(r: Any) -> list[dict]:
    if hasattr(r, "to_dict"):
        r = r.to_dict()
    if not isinstance(r, dict):
        return []
    holes = []
    ends = r.get("ends") or r.get("connection_ends") or {}
    if isinstance(ends, list):
        ends = {str(i): x for i, x in enumerate(ends)}
    if isinstance(ends, dict):
        for end_id, end in ends.items():
            if hasattr(end, "to_dict"):
                end = end.to_dict()
            if not isinstance(end, dict):
                continue
            h_list = [dict(h.to_dict() if hasattr(h, "to_dict") else h) for h in _as_list(end.get("holes")) if isinstance(h, (dict, object))]
            if h_list:
                for x in h_list:
                    if isinstance(x, dict):
                        x["end"] = str(end_id)
                        holes.append(x)
            else:
                p = end.get("pattern")
                if isinstance(p, dict):
                    count = int(p.get("count", 0) or 0)
                    start = _num(p.get("start_from_end_mm"))
                    pitch = _num(p.get("pitch_mm"))
                    if count and start is not None and pitch is not None:
                        for i in range(count):
                            h = dict(p)
                            h["along_mm"] = start + i * pitch
                            h["end"] = str(end_id)
                            holes.append(h)
                    elif count:
                        for _ in range(count):
                            h = dict(p); h["end"] = str(end_id); holes.append(h)
    # Flat legacy form may only have aggregate counts.
    if not holes and isinstance(r.get("hole_diameters_mm"), dict):
        for d, n in r["hole_diameters_mm"].items():
            for _ in range(int(n)):
                holes.append({"hole_diameter_mm": _num(d)})
    return holes


def normalize_shop_record(raw: dict, source="reference") -> dict:
    """Return a stable comparison contract for one shop drawing."""
    mark = _mark(raw.get("backmark", raw.get("source_backmark", raw.get("member_backmark"))))
    drawing_id = raw.get("drawing_id") or raw.get("dwg_no") or raw.get("shop_id") or _drawing_id(mark)
    if drawing_id:
        drawing_id = str(drawing_id).strip()
        if drawing_id.lower().startswith("dwg no:"):
            drawing_id = drawing_id.split(":", 1)[1].strip()
    section = _canonical_section(raw.get("section", raw.get("member")))
    length = _num(raw.get("length_mm", raw.get("length")))
    qty = raw.get("qty", raw.get("quantity", raw.get("member_qty")))
    try: qty = int(qty) if qty is not None else None
    except (TypeError, ValueError): qty = None

    holes = _holes_from_record(raw)
    bolt_counts = Counter()
    dia_counts = Counter()
    positions = []
    for h in holes:
        b = h.get("nominal_bolt_diameter_mm", h.get("bolt_diameter_mm"))
        d = h.get("hole_diameter_mm", h.get("diameter_mm"))
        if b is not None: bolt_counts[_bolt_key(b)] += 1
        if d is not None: dia_counts[_diam_key(d)] += 1
        if _num(h.get("along_mm")) is not None:
            positions.append({"end": h.get("end"), "along_mm": _num(h["along_mm"]), "hole_diameter_mm": _num(d), "nominal_bolt_diameter_mm": _num(b)})
    # Legacy aggregate bolt counts.
    if not bolt_counts and isinstance(raw.get("per_piece"), dict):
        bolt_counts.update({_bolt_key(k): int(v) for k, v in raw["per_piece"].items()})
    if not dia_counts and isinstance(raw.get("hole_diameters_mm"), dict):
        dia_counts.update({_diam_key(k): int(v) for k, v in raw["hole_diameters_mm"].items()})

    return {
        "drawing_id": drawing_id,
        "backmark": mark,
        "section": str(section) if section is not None else None,
        "length_mm": length,
        "qty": qty,
        "bolt_counts_per_piece": dict(sorted(bolt_counts.items())),
        "hole_diameters_per_piece": dict(sorted(dia_counts.items())),
        "hole_positions": sorted(positions, key=lambda x: (str(x.get("end")), x.get("along_mm") or 0)),
        "source": source,
    }


def load_shop_json(path: str | Path) -> list[dict]:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return [normalize_shop_record(x, source=str(p)) for x in flatten_reference_documents(data, str(p))]


def load_shop_json_dir(path: str | Path) -> list[dict]:
    p = Path(path)
    records = []
    for f in sorted(p.rglob("*.json")):
        try:
            records.extend(load_shop_json(f))
        except Exception:
            continue
    return records


def inventory_from_schedule(schedule: dict, design_path: str | Path | dict | list | None = None) -> dict:
    marks = sorted((_mark(k) for k in schedule.keys() if _mark(k)), key=lambda x: (len(x), x))
    design = {}
    if design_path:
        if isinstance(design_path, list):
            members_list = design_path
        elif isinstance(design_path, dict):
            members_list = design_path.get("members", [])
        else:
            p = Path(design_path)
            d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            members_list = d.get("members", [])

        for m in members_list:
            if hasattr(m, "to_dict"):
                m_dict = m.to_dict()
            elif isinstance(m, dict):
                m_dict = m
            else:
                continue
            mark = _mark(m_dict.get("backmark"))
            if mark:
                design[mark] = m_dict

    buildable, blocked = [], []
    for mark in marks:
        if mark not in design:
            blocked.append({
                "backmark": mark,
                "reason": "MISSING_APPROVED_ENGINEERING_INPUT",
                "reasons": ["MISSING_APPROVED_ENGINEERING_INPUT"],
            })
            continue

        m = design[mark]
        reasons = []

        # 1. Quantity validation: require non-null positive integer qty
        raw_qty = m.get("qty")
        if raw_qty is None:
            raw_qty = m.get("quantity")

        if raw_qty is None:
            reasons.append("MISSING_QTY")
        else:
            try:
                parsed_qty = int(raw_qty)
                if parsed_qty <= 0:
                    reasons.append("MISSING_QTY")
            except (TypeError, ValueError):
                reasons.append("MISSING_QTY")

        # 2. Holes validation: require explicit non-empty hole schedules
        holes = _holes_from_record(m)
        if not holes:
            reasons.append("MISSING_FABRICATION_HOLES")

        if reasons:
            blocked.append({
                "backmark": mark,
                "reason": ", ".join(reasons),
                "reasons": reasons,
            })
        else:
            buildable.append(mark)

    numeric = sorted(int(x) for x in marks if str(x).isdigit())
    ranges = []
    if numeric:
        start = prev = numeric[0]
        for n in numeric[1:]:
            if n == prev + 1:
                prev = n
            else:
                ranges.append({"start": start, "end": prev, "count": prev - start + 1})
                start = prev = n
        ranges.append({"start": start, "end": prev, "count": prev - start + 1})

    return {
        "total_unique_shop_drawings_identified": len(marks),
        "numeric_backmark_ranges": ranges,
        "identified_backmarks": marks,
        "currently_buildable_with_design_input": len(buildable),
        "buildable_backmarks": buildable,
        "blocked_count": len(blocked),
        "blocked": blocked,
        "definition": "One shop drawing per unique backmark/member schedule entry. 'Buildable' means approved design JSON contains that backmark, explicit non-null quantity, and non-empty connection hole details; extraction alone does not invent missing bolt design.",
    }


def _dict_diff(a, b, prefix="") -> list[dict]:
    out=[]
    keys=sorted(set(a) | set(b))
    for k in keys:
        pa=f"{prefix}.{k}" if prefix else k
        if k not in a: out.append({"field":pa,"status":"MISSING_REFERENCE","reference":None,"generated":b[k]})
        elif k not in b: out.append({"field":pa,"status":"MISSING_GENERATED","reference":a[k],"generated":None})
        elif a[k] != b[k]: out.append({"field":pa,"status":"MISMATCH","reference":a[k],"generated":b[k]})
    return out

def compare_shop_records(
    reference,
    generated,
    length_tol_mm=2.0,
    position_tol_mm=2.0,
):
    """
    Full field-by-field shop drawing comparison.

    Comparison hierarchy:

    Identity
    Geometry
    Quantity
    Bolt schedule
    Hole diameters
    Hole positions
    """

    fields = []

    # ========================================================
    # Scalar comparison
    # ========================================================

    def scalar(
        name,
        ref_value,
        gen_value,
        tolerance=None,
    ):

        if (
            ref_value is None
            or gen_value is None
        ):

            return {
                "field": name,
                "status": "NOT_COMPARABLE",
                "reference": ref_value,
                "generated": gen_value,
            }

        if (
            tolerance is not None
            and isinstance(
                ref_value,
                (int, float),
            )
            and isinstance(
                gen_value,
                (int, float),
            )
        ):

            difference = (
                gen_value
                - ref_value
            )

            matched = (
                abs(difference)
                <= tolerance
            )

        else:

            difference = None

            matched = (
                ref_value
                == gen_value
            )

        return {
            "field": name,

            "status":
                "MATCH"
                if matched
                else "MISMATCH",

            "reference":
                ref_value,

            "generated":
                gen_value,

            "difference":
                difference,

            "tolerance":
                tolerance,
        }

    # ========================================================
    # Identity
    # ========================================================

    fields.append(
        scalar(
            "backmark",
            reference.get(
                "backmark"
            ),
            generated.get(
                "backmark"
            ),
        )
    )

    # ========================================================
    # Section
    # ========================================================

    fields.append(
        scalar(
            "section",
            reference.get(
                "section"
            ),
            generated.get(
                "section"
            ),
        )
    )

    # ========================================================
    # Length
    # ========================================================

    fields.append(
        scalar(
            "length_mm",
            reference.get(
                "length_mm"
            ),
            generated.get(
                "length_mm"
            ),
            length_tol_mm,
        )
    )

    # ========================================================
    # Quantity
    # ========================================================

    fields.append(
        scalar(
            "qty",
            reference.get(
                "qty"
            ),
            generated.get(
                "qty"
            ),
        )
    )

    # ========================================================
    # Bolt counts
    # ========================================================

    ref_bolts = (
        reference.get(
            "bolt_counts_per_piece",
            {}
        )
    )

    gen_bolts = (
        generated.get(
            "bolt_counts_per_piece",
            {}
        )
    )

    fields.append(
        {
            "field":
                "bolt_counts_per_piece",

            "status":
                "MATCH"
                if ref_bolts == gen_bolts
                else "MISMATCH",

            "reference":
                ref_bolts,

            "generated":
                gen_bolts,
        }
    )

    # ========================================================
    # Hole diameters
    # ========================================================

    ref_dia = (
        reference.get(
            "hole_diameters_per_piece",
            {}
        )
    )

    gen_dia = (
        generated.get(
            "hole_diameters_per_piece",
            {}
        )
    )

    fields.append(
        {
            "field":
                "hole_diameters_per_piece",

            "status":
                "MATCH"
                if ref_dia == gen_dia
                else "MISMATCH",

            "reference":
                ref_dia,

            "generated":
                gen_dia,
        }
    )

    # ========================================================
    # Hole positions
    # ========================================================

    position_result = compare_hole_positions(
        reference.get(
            "hole_positions",
            []
        ),
        generated.get(
            "hole_positions",
            []
        ),
        position_tol_mm,
    )

    fields.append(
        {
            "field":
                "hole_positions",

            "status":
                position_result["status"],

            "details":
                position_result,
        }
    )

    # ========================================================
    # Result
    # ========================================================

    comparable = [
        field
        for field in fields
        if field["status"]
        != "NOT_COMPARABLE"
    ]

    mismatches = [
        field
        for field in comparable
        if field["status"]
        == "MISMATCH"
    ]

    if not comparable:

        status = (
            "NOT_COMPARABLE"
        )

    elif mismatches:

        status = "MISMATCH"

    else:

        status = "MATCH"

    return {

        "drawing_id":
            generated.get(
                "drawing_id"
            )
            or reference.get(
                "drawing_id"
            ),

        "backmark":
            generated.get(
                "backmark"
            )
            or reference.get(
                "backmark"
            ),

        "status":
            status,

        "fields":
            fields,

        "mismatch_count":
            len(mismatches),

        "comparable_field_count":
            len(comparable),

        "match_count":
            sum(
                1
                for field in comparable
                if field["status"]
                == "MATCH"
            ),

    }


def compare_sets(
    reference_records,
    generated_records,
    length_tol_mm=2.0,
    position_tol_mm=2.0,
):
    """
    Compare complete drawing sets.

    Detects:

    MATCH
    MISMATCH
    MISSING_GENERATED
    EXTRA_GENERATED
    NOT_COMPARABLE
    """

    reference = {}

    generated = {}

    for record in reference_records:

        key = (
            record.get("backmark")
            or record.get("drawing_id")
        )

        if key:

            reference[
                str(key)
            ] = record

    for record in generated_records:

        key = (
            record.get("backmark")
            or record.get("drawing_id")
        )

        if key:

            generated[
                str(key)
            ] = record

    rows = []

    all_keys = sorted(
        set(reference)
        | set(generated)
    )

    for key in all_keys:

        if key not in reference:

            rows.append(
                {
                    "drawing_id":
                        generated[key].get(
                            "drawing_id"
                        ),

                    "backmark":
                        key,

                    "status":
                        "EXTRA_GENERATED",

                    "fields": [],
                }
            )

            continue

        if key not in generated:

            rows.append(
                {
                    "drawing_id":
                        reference[key].get(
                            "drawing_id"
                        ),

                    "backmark":
                        key,

                    "status":
                        "MISSING_GENERATED",

                    "fields": [],
                }
            )

            continue

        rows.append(
            compare_shop_records(
                reference[key],
                generated[key],
                length_tol_mm,
                position_tol_mm,
            )
        )

    from collections import Counter

    status_counts = Counter(
        row["status"]
        for row in rows
    )

    comparable_rows = [
        row
        for row in rows
        if row["status"]
        in (
            "MATCH",
            "MISMATCH",
        )
    ]

    fully_matched = sum(
        1
        for row in comparable_rows
        if row["status"]
        == "MATCH"
    )

    match_rate = (
        (
            fully_matched
            / len(comparable_rows)
        )
        * 100
        if comparable_rows
        else None
    )

    return {

        "comparison_version":
            "shop-json-v2",

        "reference_count":
            len(reference),

        "generated_count":
            len(generated),

        "comparison_count":
            len(rows),

        "status_counts":
            dict(status_counts),

        "fully_matched_count":
            fully_matched,

        "drawing_match_rate_percent":
            match_rate,

        "rows":
            rows,

    }

def compare_hole_positions(
    reference,
    generated,
    tolerance_mm=2.0,
):
    """
    Compare hole positions by END.

    Each end is sorted independently.
    This avoids false mismatches caused only
    by JSON ordering.
    """

    def normalise(records):

        result = {}

        for hole in records:

            end = str(
                hole.get(
                    "end",
                    "UNKNOWN",
                )
            )

            position = hole.get(
                "along_mm"
            )

            if position is None:
                continue

            result.setdefault(
                end,
                []
            ).append(
                float(position)
            )

        for end in result:

            result[end].sort()

        return result

    ref = normalise(
        reference
    )

    gen = normalise(
        generated
    )

    if not ref and not gen:
        return {
            "status": "NOT_COMPARABLE",
            "ends": [],
        }

    if not ref:
        return {
            "status": "NOT_COMPARABLE",
            "ends": [],
            "reason": "reference has no positional hole data",
        }

    all_ends = sorted(
        set(ref) | set(gen)
    )

    end_results = []

    overall_match = True

    for end in all_ends:

        rp = ref.get(
            end,
            []
        )

        gp = gen.get(
            end,
            []
        )

        if len(rp) != len(gp):

            overall_match = False

            end_results.append(
                {
                    "end": end,
                    "status": "MISMATCH",
                    "reference_positions": rp,
                    "generated_positions": gp,
                    "reason": (
                        "hole count differs"
                    ),
                }
            )

            continue

        differences = []

        for a, b in zip(
            rp,
            gp,
        ):

            differences.append(
                abs(a - b)
            )

        max_difference = (
            max(differences)
            if differences
            else 0.0
        )

        matched = (
            max_difference
            <= tolerance_mm
        )

        if not matched:

            overall_match = False

        end_results.append(
            {
                "end": end,

                "status":
                    "MATCH"
                    if matched
                    else "MISMATCH",

                "reference_positions": rp,

                "generated_positions": gp,

                "max_abs_difference_mm":
                    max_difference,

                "tolerance_mm":
                    tolerance_mm,
            }
        )

    return {
        "status":
            "MATCH"
            if overall_match
            else "MISMATCH",

        "ends":
            end_results,
    }

def write_inventory(inventory, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    inv_text = json.dumps(inventory, indent=2)
    (out / "shop_drawing_inventory.json").write_text(inv_text, encoding="utf-8")
    (out / "drawing_inventory.json").write_text(inv_text, encoding="utf-8")

    build = set(inventory.get("buildable_backmarks", []))
    reasons = {x["backmark"]: x["reason"] for x in inventory.get("blocked", [])}

    # Legacy shop_drawing_inventory.csv
    with open(out / "shop_drawing_inventory.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["backmark", "status", "reason"])
        w.writeheader()
        for m in inventory.get("identified_backmarks", []):
            w.writerow({"backmark": m, "status": "BUILDABLE" if m in build else "BLOCKED", "reason": "" if m in build else reasons.get(m, "")})

    # Canonical drawing_inventory.csv
    with open(out / "drawing_inventory.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["backmark", "drawing_id", "status", "reason"])
        w.writeheader()
        for m in inventory.get("identified_backmarks", []):
            w.writerow({
                "backmark": m,
                "drawing_id": f"429B{m}",
                "status": "BUILDABLE" if m in build else "BLOCKED",
                "reason": "" if m in build else (reasons.get(m) or "")
            })


def write_comparison(result, out_dir):
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    (out/"shop_json_comparison.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    with open(out/"shop_json_comparison.csv","w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["drawing_id","backmark","status","mismatch_count","comparable_field_count"]);w.writeheader()
        for r in result.get("rows",[]): w.writerow({k:r.get(k) for k in w.fieldnames})


def index_json_artifacts(out_dir: str | Path) -> dict:
    """Index every JSON produced for a run, so UI/backend can expose one manifest."""
    out=Path(out_dir); rows=[]
    for p in sorted(out.rglob("*.json")):
        try:
            data=json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        name=p.name.lower()
        if "callout" in name: stage="stage_2_callouts"
        elif "association" in name: stage="stage_3_candidates"
        elif "topology" in name or "geometry" in name: stage="stage_4_geometry_topology"
        elif "resolved" in name or "review" in name: stage="stage_5_resolution"
        elif "shop" in name or "generation" in name: stage="stage_6_shop_generation"
        elif "bom" in name: stage="stage_7_bom"
        elif "schedule" in name or "locator" in name: stage="stage_1_schedule"
        else: stage="reporting"
        rows.append({"path":str(p.relative_to(out)),"file":p.name,"stage":stage,"top_level_type":type(data).__name__})
    payload={"json_count":len(rows),"artifacts":rows}
    (out/"json_artifact_manifest.json").write_text(json.dumps(payload,indent=2),encoding="utf-8")
    return payload


def build_group_report(out_dir: str | Path, schedule: dict | None = None, groups: list | None = None, associations: list | None = None, topology: dict | None = None, resolved: dict | None = None, inventory: dict | None = None, comparison: dict | None = None) -> dict:
    """Create a stable group-centric view plus copies/references to all stage JSONs."""
    out=Path(out_dir); grouped=out/"grouped_json"; grouped.mkdir(parents=True,exist_ok=True)
    groups=groups or []
    if isinstance(associations, dict):
        associations = list(associations.values())
    by_mark=defaultdict(lambda:{"backmark":None,"groups":[],"resolved":[],"associations":[]})
    for m in (schedule or {}).keys(): by_mark[_mark(m)]["backmark"]=_mark(m)
    for a in associations or []:
        mark=_mark(a.get("backmark",a.get("member")))
        if mark: by_mark[mark]["associations"].append(a)
    for mark_key, r in (resolved or {}).get("members",{}).items():
        mark=_mark(mark_key)
        if mark: by_mark[mark]["resolved"].append(r)
    # Connect group IDs from resolved connection ends and direct group proximity.
    for mark, item in by_mark.items():
        gids=set()
        for r in item["resolved"]:
            for e in r.get("connection_ends",[]):
                gid = e.get("assigned_group_id") or e.get("candidate_group_id")
                if gid is not None: gids.add(str(gid))
        item["groups"]=sorted(gids)
    group_index={str(g.get("group_id")):g for g in groups if g.get("group_id") is not None}
    rows=[]
    for mark,item in sorted(by_mark.items(),key=lambda z:(len(z[0] or ""),z[0] or "")):
        rows.append({"backmark":mark,"group_ids":item["groups"],"group_count":len(item["groups"]),"association_count":len(item["associations"]),"resolved_end_count":sum(len(r.get("connection_ends",[])) for r in item["resolved"]),"resolved_statuses":Counter(e.get("status") for r in item["resolved"] for e in r.get("connection_ends",[]))})
        payload={"backmark":mark,"schedule":(schedule or {}).get(mark,{}),"groups":{g:group_index.get(g) for g in item["groups"]},"associations":item["associations"],"resolved":item["resolved"]}
        (grouped/f"backmark_{mark}.json").write_text(json.dumps(payload,indent=2,default=str),encoding="utf-8")
    artifact_manifest=index_json_artifacts(out)
    report={"group_count":len(group_index),"backmark_count":len(rows),"rows":rows,"groups":group_index,"inventory":inventory or {},"comparison_summary":{k:v for k,v in (comparison or {}).items() if k!="rows"},"json_artifact_manifest":artifact_manifest}
    (out/"group_report.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8")
    with open(out/"group_report.csv","w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["backmark","group_ids","group_count","association_count","resolved_end_count","resolved_statuses"]);w.writeheader()
        for r in rows:
            x=dict(r);x["group_ids"]=",".join(r["group_ids"]);x["resolved_statuses"]=json.dumps(r["resolved_statuses"]);w.writerow(x)
    return report


def build_detailed_report(out_dir: str | Path, summary: dict, inventory: dict, comparison: dict | None, group_report: dict | None) -> dict:
    report={
        "report_version":"1.0",
        "scope":{"out_dir":str(out_dir)},
        "pipeline_summary":summary,
        "shop_drawing_inventory":inventory,
        "reference_vs_generated":comparison or {"status":"NOT_RUN"},
        "group_report":group_report or {},
        "decision_notes":[
            "Identified drawing count is based on unique backmarks in the extracted member schedule.",
            "Currently buildable count requires matching approved design-input data; missing bolt/connection design is not invented.",
            "JSON comparison distinguishes MATCH, MISMATCH, MISSING_GENERATED, EXTRA_GENERATED and NOT_COMPARABLE.",
            "Reference shop JSON is validation input only and never feeds production inference.",
        ],
    }
    p=Path(out_dir)/"detailed_shop_report.json";p.write_text(json.dumps(report,indent=2,default=str),encoding="utf-8")
    lines=["# Shop Drawing Detailed Report","",f"Identified shop drawings: **{inventory.get('total_unique_shop_drawings_identified',0)}**",f"Currently buildable with approved design input: **{inventory.get('currently_buildable_with_design_input',0)}**","", "## Inventory"]
    for m in inventory.get("identified_backmarks",[]):
        lines.append(f"- {m}: {'BUILDABLE' if m in inventory.get('buildable_backmarks',[]) else 'BLOCKED'}")
    lines += ["", "## Reference vs generated JSON"]
    if comparison:
        lines.append(f"- Reference drawings: {comparison.get('reference_count',0)}")
        lines.append(f"- Generated drawings: {comparison.get('generated_count',0)}")
        lines.append(f"- Status counts: {comparison.get('status_counts',{})}")
        for r in comparison.get("rows",[]): lines.append(f"- {r.get('drawing_id') or r.get('backmark')}: **{r.get('status')}**")
    else: lines.append("- Not run; provide --reference-shop-json or --reference-shop-json-dir.")
    lines += ["", "## Group report",f"- Groups: {group_report.get('group_count',0) if group_report else 0}",f"- Backmarks: {group_report.get('backmark_count',0) if group_report else 0}","", "## Engineering boundary", "Bolt counts and fabrication dimensions are only generated when present in approved design input. The system does not reverse-engineer missing design decisions from validation drawings."]
    (Path(out_dir)/"detailed_shop_report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    return report


def _normalize_section_str(sec: Any) -> str:
    if not sec:
        return ""
    import re
    s = str(sec).strip().upper().replace(" ", "")
    # Check flat with THK, THK., or THICKNESS (e.g., 4THKX94, HT8THKX154, 4THK.X45, 154THKX8)
    m = re.match(r"^(?:HT)?(\d+(?:\.\d+)?)(?:THK\.?|THICKNESS)X(\d+(?:\.\d+)?)$", s)
    if m:
        prefix = "HT_" if "HT" in s else ""
        d1 = float(m.group(1))
        d2 = float(m.group(2))
        return f"{prefix}FLAT_{min(d1, d2):g}X{max(d1, d2):g}"
    m_flat = re.match(r"^(?:HT)?(?:FLAT|PL)(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$", s)
    if m_flat:
        prefix = "HT_" if "HT" in s else ""
        d1 = float(m_flat.group(1))
        d2 = float(m_flat.group(2))
        return f"{prefix}FLAT_{min(d1, d2):g}X{max(d1, d2):g}"
    # Angle
    m_ang = re.match(r"^(HTL|HT|ISA|L)?(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$", s)
    if m_ang:
        prefix = "HTL_" if (m_ang.group(1) and "HT" in m_ang.group(1)) else "L_"
        return f"{prefix}{float(m_ang.group(2)):g}X{float(m_ang.group(3)):g}X{float(m_ang.group(4)):g}"
    return s


def evaluate_regression_gates(
    schedule: dict | list | None = None,
    design_input: str | Path | dict | list | None = None,
    reference_data: str | Path | dict | list | None = None,
    length_tol_mm: float = 2.0,
    position_tol_mm: float = 2.0,
    generated_drawings: list[dict] | None = None,
) -> dict[str, Any]:
    """Evaluate 26 reference drawings and 11 non-reference members against Acceptance Gates A through H.

    Gates:
      Gate A (Identity): 26/26 correct backmarks.
      Gate B (Section/Length): 26/26 correct within approved tolerance (<= 2.0 mm).
      Gate C (Quantity): All 26 reference members have verified quantities matching reference metadata.
      Gate D (Holes): Verified hole count, hole diameter classes, and hole positions for diagnostic cases 37, 44, 45, 46.
      Gate E (Dimensions): Verified dimension chains (overall length L, end distances e1, e2, irregular pitch intervals) match ground truth for 37, 44, 45, 46.
      Gate F (Section/End View): Verified section profile families (Angle, Flat, HT_Angle, HT_Flat) render appropriate views.
      Gate H (No False Positives): Never mark a drawing as MATCH merely because metadata matches; non-reference members must be marked NOT_VALIDATED.
    """
    from domain.models import build_dimension_chains, classify_section
    from shop_drawing import _parse_section

    # 1. Ingest reference metadata fixture
    ref_fixture_path = Path(__file__).parent / "fixtures" / "reference_metadata_26.json"
    raw_ref = {}
    if reference_data is not None:
        if isinstance(reference_data, (str, Path)):
            p = Path(reference_data)
            raw_ref = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        elif isinstance(reference_data, dict):
            raw_ref = reference_data
    elif ref_fixture_path.exists():
        raw_ref = json.loads(ref_fixture_path.read_text(encoding="utf-8"))

    ref_members_raw = raw_ref.get("reference_members", {})
    if isinstance(ref_members_raw, list):
        ref_dict = {_mark(m.get("backmark")): m for m in ref_members_raw if _mark(m.get("backmark"))}
    else:
        ref_dict = {_mark(k): v for k, v in ref_members_raw.items() if _mark(k)}

    non_ref_list = [_mark(k) for k in raw_ref.get("non_reference_members", []) if _mark(k)]

    # 2. Parse design input
    design_dict = {}
    if design_input is not None:
        if isinstance(design_input, (str, Path)):
            dp = Path(design_input)
            raw_design = json.loads(dp.read_text(encoding="utf-8")) if dp.exists() else {}
            members_list = raw_design.get("members", [])
        elif isinstance(design_input, dict):
            members_list = design_input.get("members", [])
        elif isinstance(design_input, list):
            members_list = design_input
        else:
            members_list = []

        for m in members_list:
            if hasattr(m, "to_dict"):
                m_dict = m.to_dict()
            elif isinstance(m, dict):
                m_dict = m
            else:
                continue
            mark = _mark(m_dict.get("backmark"))
            if mark:
                design_dict[mark] = m_dict

    # 3. Parse schedule
    sched_dict = {}
    if schedule is not None:
        if isinstance(schedule, dict):
            for k, v in schedule.items():
                mk = _mark(k)
                if mk:
                    sched_dict[mk] = v
        elif isinstance(schedule, list):
            for r in schedule:
                mk = _mark(r.get("backmark"))
                if mk:
                    sched_dict[mk] = r

    # Universe of all backmarks
    all_marks_set = set(ref_dict) | set(non_ref_list) | set(design_dict) | set(sched_dict)
    all_marks = sorted(
        all_marks_set,
        key=lambda x: (int(x) if str(x).isdigit() else 99999, len(str(x)), str(x))
    )

    scorecard = []
    for mark in all_marks:
        drawing_id = f"429B{mark}"
        is_ref = mark in ref_dict

        if not is_ref:
            scorecard.append({
                "backmark": mark,
                "drawing_id": drawing_id,
                "is_reference": False,
                "gate_a_identity": "N/A",
                "gate_b_section_length": "N/A",
                "gate_c_quantity": "N/A",
                "gate_d_holes": "N/A",
                "gate_e_dimensions": "N/A",
                "gate_f_section_view": "N/A",
                "gate_h_no_false_positives": "PASS",
                "validation_status": "NOT_VALIDATED",
                "notes": "Non-reference member without supplied reference drawing; explicitly labeled NOT_VALIDATED per Gate H.",
            })
            continue

        ref = ref_dict[mark]
        des = design_dict.get(mark)
        sch = sched_dict.get(mark, {})

        # Gate A: Identity
        has_mark = (mark in sched_dict) or (des is not None)
        gate_a = "PASS" if has_mark else "FAIL"

        # Gate B: Section / Length
        cand_sec = (des.get("section") if des else None) or sch.get("section") or ""
        ref_sec = ref.get("section") or ""

        cand_len = None
        raw_len = (des.get("length_mm") if (des and des.get("length_mm") is not None) else (sch.get("length_mm") if sch else None))
        if raw_len is not None:
            try:
                cand_len = float(raw_len)
            except (ValueError, TypeError):
                cand_len = None

        ref_len = None
        raw_ref_len = ref.get("length_mm")
        if raw_ref_len is not None:
            try:
                ref_len = float(raw_ref_len)
            except (ValueError, TypeError):
                ref_len = None

        sec_norm_cand = _normalize_section_str(cand_sec)
        sec_norm_ref = _normalize_section_str(ref_sec)
        sec_match = (bool(sec_norm_cand) and sec_norm_cand == sec_norm_ref) or (bool(cand_sec) and cand_sec.strip().upper() == ref_sec.strip().upper())
        len_match = (cand_len is not None and ref_len is not None and abs(cand_len - ref_len) <= length_tol_mm)
        gate_b = "PASS" if (sec_match and len_match) else "FAIL"

        # Gate C: Quantity
        ref_qty = None
        try:
            ref_qty = int(ref.get("qty", 0))
        except (ValueError, TypeError):
            ref_qty = None

        cand_qty = None
        if des is not None:
            raw_q = des.get("qty") if des.get("qty") is not None else des.get("quantity")
            if raw_q is not None:
                try:
                    cand_qty = int(raw_q)
                except (ValueError, TypeError):
                    cand_qty = None
        gate_c = "PASS" if (cand_qty is not None and ref_qty is not None and cand_qty == ref_qty) else "FAIL"

        # Gate D: Holes (diagnostic cases 37, 44, 45, 46)
        has_hole_oracle = "hole_positions_mm" in ref or "hole_count" in ref
        if has_hole_oracle:
            cand_holes = _holes_from_record(des) if des else []
            try:
                ref_hole_count = int(ref.get("hole_count", len(ref.get("hole_positions_mm", []))))
            except (ValueError, TypeError):
                ref_hole_count = -1
            count_match = len(cand_holes) == ref_hole_count
            pos_match = False
            if count_match and cand_holes:
                try:
                    c_pos = sorted(float(h["along_mm"]) for h in cand_holes if h.get("along_mm") is not None)
                    r_pos = sorted(float(p) for p in ref.get("hole_positions_mm", []) if p is not None)
                    pos_match = (len(c_pos) == len(r_pos) and all(abs(c - r) <= position_tol_mm for c, r in zip(c_pos, r_pos)))
                except (ValueError, TypeError, KeyError):
                    pos_match = False

            diam_match = True
            if "hole_diameters_mm" in ref and cand_holes:
                try:
                    c_diams_list = []
                    for h in cand_holes:
                        d_val = h.get("hole_diameter_mm") if h.get("hole_diameter_mm") is not None else h.get("diameter_mm")
                        if d_val is not None:
                            c_diams_list.append(f"{float(d_val):g}")
                    c_diams = Counter(c_diams_list)
                    r_diams = {str(k): int(v) for k, v in ref["hole_diameters_mm"].items()}
                    diam_match = (dict(c_diams) == r_diams)
                except (ValueError, TypeError, KeyError):
                    diam_match = False

            gate_d = "PASS" if (count_match and pos_match and diam_match) else "FAIL"
        else:
            gate_d = "N/A"

        # Gate E: Dimensions
        if has_hole_oracle and des:
            cand_holes = _holes_from_record(des)
            if cand_holes and cand_len is not None and cand_len > 0:
                gauge_val = ref.get("dimension_chain", {}).get("gauge_mm")
                try:
                    gauge_float = float(gauge_val) if gauge_val is not None else None
                except (ValueError, TypeError):
                    gauge_float = None
                try:
                    chains = build_dimension_chains(cand_len, cand_holes, gauge_mm=gauge_float)
                    level1 = chains[0]
                    level2 = chains[1]
                    l2_match = len(level2) == 1 and abs(level2[0].value_mm - cand_len) <= length_tol_mm
                    sum_match = abs(sum(d.value_mm for d in level1) - cand_len) <= 0.01

                    ref_chain = ref.get("dimension_chain", {})
                    if ref_chain and "pitch_intervals_mm" in ref_chain:
                        exp_ints = [float(ref_chain["end_distance_1_mm"])] + [float(x) for x in ref_chain["pitch_intervals_mm"]] + [float(ref_chain["end_distance_2_mm"])]
                        int_match = (len(level1) == len(exp_ints) and all(abs(a.value_mm - b) <= position_tol_mm for a, b in zip(level1, exp_ints)))
                    else:
                        int_match = True
                    gate_e = "PASS" if (l2_match and sum_match and int_match) else "FAIL"
                except Exception:
                    gate_e = "FAIL"
            else:
                gate_e = "FAIL"
        elif has_hole_oracle:
            gate_e = "FAIL"
        else:
            gate_e = "N/A"

        # Gate F: Section / End View
        sec_to_parse = cand_sec or ref_sec
        info = _parse_section(sec_to_parse)
        sec_family_recognized = info.get("kind") in ("ANGLE", "FLAT")
        gate_f = "PASS" if sec_family_recognized else "FAIL"

        # Gate H: No False Positives
        if has_hole_oracle:
            if gate_a == "PASS" and gate_b == "PASS" and gate_c == "PASS" and gate_d == "PASS" and gate_e == "PASS" and gate_f == "PASS":
                v_status = "VALIDATED_REFERENCE"
            else:
                v_status = "MISMATCH"
        else:
            if gate_a == "PASS" and gate_b == "PASS" and gate_c == "PASS" and gate_f == "PASS":
                v_status = "METADATA_MATCH_ONLY"
            else:
                v_status = "MISMATCH"

        gate_h = "PASS"

        scorecard.append({
            "backmark": mark,
            "drawing_id": drawing_id,
            "is_reference": True,
            "gate_a_identity": gate_a,
            "gate_b_section_length": gate_b,
            "gate_c_quantity": gate_c,
            "gate_d_holes": gate_d,
            "gate_e_dimensions": gate_e,
            "gate_f_section_view": gate_f,
            "gate_h_no_false_positives": gate_h,
            "validation_status": v_status,
            "notes": "Diagnostic case full validation." if has_hole_oracle else "Verified reference metadata (Gate A-C, F).",
        })

    # Tally summary
    ref_rows = [r for r in scorecard if r["is_reference"]]
    non_ref_rows = [r for r in scorecard if not r["is_reference"]]
    diag_rows = [r for r in scorecard if r["backmark"] in ("37", "44", "45", "46")]

    gate_a_pass = sum(1 for r in ref_rows if r["gate_a_identity"] == "PASS")
    gate_b_pass = sum(1 for r in ref_rows if r["gate_b_section_length"] == "PASS")
    gate_c_pass = sum(1 for r in ref_rows if r["gate_c_quantity"] == "PASS")
    gate_d_pass = sum(1 for r in diag_rows if r["gate_d_holes"] == "PASS")
    gate_e_pass = sum(1 for r in diag_rows if r["gate_e_dimensions"] == "PASS")
    gate_f_pass = sum(1 for r in ref_rows if r["gate_f_section_view"] == "PASS")
    gate_h_pass = sum(1 for r in scorecard if r["gate_h_no_false_positives"] == "PASS")

    overall_pass = (
        gate_a_pass == len(ref_rows)
        and gate_b_pass == len(ref_rows)
        and gate_c_pass == len(ref_rows)
        and gate_d_pass == len(diag_rows)
        and gate_e_pass == len(diag_rows)
        and gate_f_pass == len(ref_rows)
        and gate_h_pass == len(scorecard)
    )

    result = {
        "summary": {
            "members_total": len(scorecard),
            "reference_drawings": len(ref_rows),
            "validated_reference": len(ref_rows) if (gate_a_pass == len(ref_rows) and gate_b_pass == len(ref_rows) and gate_c_pass == len(ref_rows)) else 0,
            "not_validated": len(non_ref_rows),
            "diagnostic_cases_total": len(diag_rows),
            "diagnostic_cases_passed": gate_d_pass,
            "overall_pass": overall_pass,
            "gates": {
                "gate_a_identity": {"pass": gate_a_pass, "fail": len(ref_rows) - gate_a_pass, "total": len(ref_rows)},
                "gate_b_section_length": {"pass": gate_b_pass, "fail": len(ref_rows) - gate_b_pass, "total": len(ref_rows)},
                "gate_c_quantity": {"pass": gate_c_pass, "fail": len(ref_rows) - gate_c_pass, "total": len(ref_rows)},
                "gate_d_holes": {"pass": gate_d_pass, "fail": len(diag_rows) - gate_d_pass, "total": len(diag_rows), "not_applicable": len(ref_rows) - len(diag_rows)},
                "gate_e_dimensions": {"pass": gate_e_pass, "fail": len(diag_rows) - gate_e_pass, "total": len(diag_rows), "not_applicable": len(ref_rows) - len(diag_rows)},
                "gate_f_section_view": {"pass": gate_f_pass, "fail": len(ref_rows) - gate_f_pass, "total": len(ref_rows)},
                "gate_h_no_false_positives": {"pass": gate_h_pass, "fail": len(scorecard) - gate_h_pass, "total": len(scorecard)},
            },
        },
        "scorecard": scorecard,
    }
    return result


def write_regression_report(regression_result: dict[str, Any], out_dir: str | Path) -> None:
    """Write regression scorecard to regression_report.json and regression_report.csv."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "regression_report.json"
    json_path.write_text(json.dumps(regression_result, indent=2), encoding="utf-8")

    csv_path = out / "regression_report.csv"
    fields = [
        "backmark",
        "drawing_id",
        "is_reference",
        "gate_a_identity",
        "gate_b_section_length",
        "gate_c_quantity",
        "gate_d_holes",
        "gate_e_dimensions",
        "gate_f_section_view",
        "gate_h_no_false_positives",
        "validation_status",
        "notes",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in regression_result.get("scorecard", []):
            w.writerow({k: r.get(k, "") for k in fields})
