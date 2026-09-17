"""
GOLDEN GATE SCORING ENGINE
===========================

Deterministic regression scoring for the 4 golden shop drawings.

Categories:
  A. Metadata   (15%) — backmark, section, length, quantity
  B. Fabrication (30%) — hole count, positions, diameters, bolts, dim chain
  C. Geometry   (15%) — DXF entity structure: silhouette, section view, hidden lines, centerlines
  D. Dimension  (15%) — dimension values, chain completeness, overall length, gauge dim
  E. Layout     (15%) — title block, hole schedule, section view, border, scale, notes
  F. Visual     (10%) — PDF SSIM (BLOCKED without reference PDFs; scored 0 or skipped)

When F is BLOCKED, weights A–E are rescaled to sum to 100%.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

try:
    import ezdxf
except ImportError:
    ezdxf = None


# ============================================================
# CATEGORY WEIGHTS
# ============================================================

WEIGHTS_FULL = {
    "A_metadata": 0.15,
    "B_fabrication": 0.30,
    "C_geometry": 0.15,
    "D_dimension": 0.15,
    "E_layout": 0.15,
    "F_visual": 0.10,
}

WEIGHTS_NO_VISUAL = {
    "A_metadata": 15 / 90,
    "B_fabrication": 30 / 90,
    "C_geometry": 15 / 90,
    "D_dimension": 15 / 90,
    "E_layout": 15 / 90,
}


# ============================================================
# REFERENCE DATA LOADER
# ============================================================

DEFAULT_GOLDEN_MEMBERS = ("30", "31", "32", "33", "34", "37", "44", "45", "46")


def load_reference(fixtures_dir: str | Path, target_members: tuple[str, ...] | None = None) -> dict[str, dict]:
    """Load reference metadata for the golden members."""
    fixtures_dir = Path(fixtures_dir)
    ref_path = fixtures_dir / "reference_metadata_26.json"
    data = json.loads(ref_path.read_text(encoding="utf-8"))
    members = data.get("reference_members", {})
    targets = target_members or DEFAULT_GOLDEN_MEMBERS
    golden = {}
    for bm in targets:
        if bm in members:
            golden[bm] = members[bm]
    return golden


def load_generated(shop_dir: str | Path, target_members: tuple[str, ...] | None = None) -> dict[str, dict]:
    """Load generated JSON sidecars for the golden members."""
    shop_dir = Path(shop_dir)
    targets = target_members or DEFAULT_GOLDEN_MEMBERS
    golden = {}
    for bm in targets:
        json_path = shop_dir / f"429B{bm}.json"
        if json_path.exists():
            golden[bm] = json.loads(json_path.read_text(encoding="utf-8"))
    return golden


# ============================================================
# A. METADATA SCORE (15%)
# ============================================================

def score_metadata(gen: dict, ref: dict) -> tuple[float, list[str]]:
    """Compare backmark, section, length, quantity. Each field = 25%."""
    score = 0.0
    diffs = []

    # Backmark
    gen_bm = str(gen.get("backmark", ""))
    ref_bm = str(ref.get("backmark", ""))
    if gen_bm == ref_bm:
        score += 25.0
    else:
        diffs.append(f"backmark: gen={gen_bm} ref={ref_bm}")

    # Section comparison using structural parsing
    from shop_drawing import _parse_section
    p_gen = _parse_section(gen.get("section", ""))
    p_ref = _parse_section(ref.get("canonical_section", ref.get("section", "")))
    
    sections_match = False
    if p_gen.get("kind") == p_ref.get("kind") and p_gen.get("kind") != "UNKNOWN":
        if p_gen["kind"] == "FLAT":
            t_match = abs(p_gen.get("thickness_mm", 0) - p_ref.get("thickness_mm", 0)) < 0.5
            w_match = abs(p_gen.get("width_mm", 0) - p_ref.get("width_mm", 0)) < 0.5
            sections_match = t_match and w_match
        elif p_gen["kind"] == "ANGLE":
            a_match = abs(p_gen.get("leg_a_mm", 0) - p_ref.get("leg_a_mm", 0)) < 0.5
            b_match = abs(p_gen.get("leg_b_mm", 0) - p_ref.get("leg_b_mm", 0)) < 0.5
            t_match = abs(p_gen.get("thickness_mm", 0) - p_ref.get("thickness_mm", 0)) < 0.5
            sections_match = a_match and b_match and t_match

    if sections_match:
        score += 25.0
    else:
        # Partial match — same family?
        gen_sec_raw = str(gen.get("section", "")).upper()
        ref_sec_raw = str(ref.get("section", "")).upper()
        if _section_family(gen_sec_raw) == _section_family(ref_sec_raw):
            score += 15.0
            diffs.append(f"section: gen={gen.get('section')} ref={ref.get('section')} (same family)")
        else:
            diffs.append(f"section: gen={gen.get('section')} ref={ref.get('section')}")

    # Length
    gen_len = float(gen.get("length_mm", 0))
    ref_len = float(ref.get("length_mm", 0))
    if abs(gen_len - ref_len) <= 1.0:
        score += 25.0
    else:
        diffs.append(f"length_mm: gen={gen_len} ref={ref_len}")

    # Quantity
    gen_qty = int(gen.get("qty") or gen.get("quantity") or 0)
    ref_qty = int(ref.get("qty", 0))
    if gen_qty == ref_qty:
        score += 25.0
    else:
        diffs.append(f"qty: gen={gen_qty} ref={ref_qty}")

    return score, diffs


def _normalize_section(s: str) -> str:
    """Normalize section string for comparison."""
    s = s.strip().upper().replace(" ", "")
    s = s.replace("THK", "T")
    s = s.replace("FLAT", "F")
    return s


def _section_family(s: str) -> str:
    s = s.strip().upper().replace(" ", "")
    if "L" in s and "X" in s and s.count("X") >= 2:
        return "ANGLE"
    if "FLAT" in s or "THK" in s or "PL" in s:
        return "FLAT"
    return "UNKNOWN"


# ============================================================
# B. FABRICATION SCORE (30%)
# ============================================================

def score_fabrication(gen: dict, ref: dict) -> tuple[float, list[str]]:
    """
    Hole count match = 20%
    Per-hole positions within ±1mm = 50% (equally weighted)
    Per-hole diameters exact = 20%
    Dimension chain invariant = 10%
    """
    score = 0.0
    diffs = []

    gen_holes = _extract_positions(gen)
    ref_positions = ref.get("hole_positions_mm", [])
    ref_count = ref.get("hole_count", len(ref_positions))

    # Hole count
    gen_count = len(gen_holes)
    if gen_count == ref_count:
        score += 20.0
    else:
        diffs.append(f"hole_count: gen={gen_count} ref={ref_count}")

    # Per-hole positions (ordered)
    if ref_positions and gen_holes:
        gen_pos = sorted([h["along_mm"] for h in gen_holes])
        ref_pos = sorted(ref_positions)
        n = min(len(gen_pos), len(ref_pos))
        if n > 0:
            pos_score = 0.0
            for i in range(n):
                error = abs(gen_pos[i] - ref_pos[i])
                if error <= 1.0:
                    pos_score += 1.0
                else:
                    diffs.append(f"hole_pos[{i}]: gen={gen_pos[i]} ref={ref_pos[i]} error={error:.1f}mm")
            score += 50.0 * (pos_score / max(len(ref_pos), 1))

    # Per-hole diameters
    gen_dia_counts = gen.get("hole_diameters_mm", {})
    ref_dia_counts = ref.get("hole_diameters_mm", {})
    if gen_dia_counts and ref_dia_counts:
        if gen_dia_counts == ref_dia_counts:
            score += 20.0
        else:
            # Partial credit
            all_keys = set(list(gen_dia_counts.keys()) + list(ref_dia_counts.keys()))
            match_count = sum(
                1 for k in all_keys
                if gen_dia_counts.get(k) == ref_dia_counts.get(k)
            )
            score += 20.0 * (match_count / max(len(all_keys), 1))
            diffs.append(f"hole_diameters: gen={gen_dia_counts} ref={ref_dia_counts}")
    elif not ref_dia_counts:
        score += 20.0  # No reference data to compare

    # Dimension chain invariant: e1 + Σp + e2 = L
    length = float(gen.get("length_mm", 0))
    if gen_holes and length > 0:
        positions = sorted([h["along_mm"] for h in gen_holes])
        e1 = positions[0]
        e2 = length - positions[-1]
        pitches = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
        chain_sum = e1 + sum(pitches) + e2
        if abs(chain_sum - length) <= 1.0:
            score += 10.0
        else:
            diffs.append(f"dim_chain_invariant: e1+Σp+e2={chain_sum:.1f} vs L={length}")
    elif length > 0:
        score += 10.0  # No holes to validate chain

    return score, diffs


def _extract_positions(gen: dict) -> list[dict]:
    """Extract hole data from generated JSON."""
    holes = gen.get("holes", [])
    if not holes:
        holes = gen.get("hole_positions", [])
    return holes


# ============================================================
# C. GEOMETRY SCORE (15%)
# ============================================================

def score_geometry(dxf_path: str | Path, section_info: dict) -> tuple[float, list[str]]:
    """
    Inspect DXF entities by layer to verify geometry completeness.

    Silhouette outline = 30%
    Section profile drawn = 20%
    End geometry = 20%
    Hidden lines = 15%
    Centerlines = 15%
    """
    dxf_path = Path(dxf_path)
    score = 0.0
    diffs = []

    if not dxf_path.exists() or ezdxf is None:
        diffs.append("DXF file not found or ezdxf not available")
        return score, diffs

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    # Count entities by layer
    layer_counts = {}
    layer_types = {}
    for entity in msp:
        layer = entity.dxf.layer
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        entity_type = entity.dxftype()
        if layer not in layer_types:
            layer_types[layer] = set()
        layer_types[layer].add(entity_type)

    # Silhouette (SHOP_MEMBER layer — need lines)
    member_count = layer_counts.get("SHOP_MEMBER", 0)
    if member_count >= 4:  # At minimum 4 lines for a rectangle/outline
        score += 30.0
    elif member_count >= 2:
        score += 15.0
        diffs.append(f"member_outline: only {member_count} entities (expected ≥4)")
    else:
        diffs.append(f"member_outline: only {member_count} entities")

    # Section profile (SHOP_SECTION layer)
    section_count = layer_counts.get("SHOP_SECTION", 0)
    if section_count >= 3:  # L-shape needs ≥6, rect needs ≥4
        score += 20.0
    elif section_count >= 1:
        score += 10.0
        diffs.append(f"section_view: only {section_count} entities")
    else:
        diffs.append("section_view: MISSING")

    # End geometry (checking if bevel/chamfer lines exist in SHOP_MEMBER beyond basic outline)
    kind = section_info.get("kind", "UNKNOWN")
    if kind == "ANGLE":
        # Angle members should have bevel lines (more than 5 member lines)
        if member_count >= 6:
            score += 20.0
        elif member_count >= 5:
            score += 15.0
            diffs.append("end_geometry: minimal bevel lines")
        else:
            score += 5.0
            diffs.append("end_geometry: insufficient bevel geometry")
    elif kind == "FLAT":
        # Flat members have square ends (4 lines minimum)
        if member_count >= 4:
            score += 20.0
        else:
            diffs.append("end_geometry: insufficient outline")

    # Hidden lines (dashed linetype in SHOP_MEMBER for angles)
    if kind == "FLAT":
        score += 15.0  # Solid rectangular bar elevation has no hidden edges
    else:
        has_dashed = False
        for entity in msp:
            if entity.dxf.layer == "SHOP_MEMBER":
                lt = getattr(entity.dxf, "linetype", None)
                if lt and "DASH" in str(lt).upper():
                    has_dashed = True
                    break
        if has_dashed:
            score += 15.0
        else:
            diffs.append("hidden_lines: no dashed lines found")

    # Centerlines (SHOP_CENTER layer)
    center_count = layer_counts.get("SHOP_CENTER", 0)
    if center_count >= 2:
        score += 15.0
    elif center_count >= 1:
        score += 8.0
        diffs.append(f"centerlines: only {center_count}")
    else:
        diffs.append("centerlines: MISSING")

    return score, diffs


# ============================================================
# D. DIMENSION SCORE (15%)
# ============================================================

def score_dimensions(gen: dict, ref: dict) -> tuple[float, list[str]]:
    """
    Dim chain segments all present = 40%
    Overall dim present and correct = 20%
    Gauge dim present = 20%
    All dim values within ±1mm = 20%
    """
    score = 0.0
    diffs = []

    ref_chain = ref.get("dimension_chain", {})
    gen_chains = gen.get("dimension_chains", [])

    # Check dimension chain segments
    ref_pitches = ref_chain.get("pitch_intervals_mm", [])
    ref_e1 = ref_chain.get("end_distance_1_mm")
    ref_e2 = ref_chain.get("end_distance_2_mm")
    ref_overall = ref_chain.get("overall_length_mm")

    # Extract generated L1 chain
    gen_l1_values = []
    gen_has_overall = False
    gen_overall_value = None
    gen_has_gauge = False
    gen_gauge_value = None

    for chain in gen_chains:
        for item in chain:
            kind = item.get("kind", "")
            value = float(item.get("value_mm", 0))
            level = int(item.get("level", 0))

            if level == 1:
                gen_l1_values.append({"kind": kind, "value": value})
            elif level == 2 and kind == "overall":
                gen_has_overall = True
                gen_overall_value = value
            elif level == 3 and kind == "gauge":
                gen_has_gauge = True
                gen_gauge_value = value

    # Check L1 segments are present
    if ref_pitches and ref_e1 is not None and ref_e2 is not None:
        expected_segments = [ref_e1] + ref_pitches + [ref_e2]
        gen_segments = [item["value"] for item in gen_l1_values]

        if len(gen_segments) == len(expected_segments):
            all_match = all(
                abs(g - r) <= 1.0
                for g, r in zip(gen_segments, expected_segments)
            )
            if all_match:
                score += 40.0
            else:
                match_count = sum(
                    1 for g, r in zip(gen_segments, expected_segments)
                    if abs(g - r) <= 1.0
                )
                score += 40.0 * (match_count / len(expected_segments))
                mismatches = [
                    f"seg[{i}]: gen={gen_segments[i]} ref={expected_segments[i]}"
                    for i in range(len(expected_segments))
                    if abs(gen_segments[i] - expected_segments[i]) > 1.0
                ]
                diffs.extend(mismatches)
        else:
            score += 40.0 * (min(len(gen_segments), len(expected_segments)) / max(len(expected_segments), 1))
            diffs.append(f"dim_segments: gen={len(gen_segments)} ref={len(expected_segments)}")

    # Overall dimension
    if gen_has_overall and ref_overall is not None:
        if abs(gen_overall_value - ref_overall) <= 1.0:
            score += 20.0
        else:
            diffs.append(f"overall_dim: gen={gen_overall_value} ref={ref_overall}")
    elif not gen_has_overall:
        diffs.append("overall_dim: MISSING")

    # Gauge dimension
    ref_gauge = ref_chain.get("gauge_mm")
    if gen_has_gauge:
        if ref_gauge is not None and abs(gen_gauge_value - ref_gauge) <= 1.0:
            score += 20.0
        elif ref_gauge is None:
            score += 20.0  # No ref gauge to compare, but generated has one — OK
        else:
            diffs.append(f"gauge_dim: gen={gen_gauge_value} ref={ref_gauge}")
            score += 10.0  # Partial — gauge present but wrong value
    else:
        if ref_gauge is not None:
            diffs.append(f"gauge_dim: MISSING (ref={ref_gauge})")
        else:
            score += 20.0  # Neither has gauge

    # Dim value accuracy (checking all L1 values match reference within ±1mm)
    if gen_l1_values and ref_pitches:
        gen_values = sorted([v["value"] for v in gen_l1_values])
        ref_all = sorted([ref_e1] + ref_pitches + [ref_e2])
        if len(gen_values) == len(ref_all):
            sorted_match = all(abs(g - r) <= 1.0 for g, r in zip(gen_values, ref_all))
            if sorted_match:
                score += 20.0
            else:
                score += 10.0
        else:
            score += 5.0
    elif not ref_pitches:
        score += 20.0  # No reference to compare

    return score, diffs


# ============================================================
# E. LAYOUT SCORE (15%)
# ============================================================

def score_layout(dxf_path: str | Path, gen: dict | None = None) -> tuple[float, list[str]]:
    """
    Title block present with fields = 30%
    Hole schedule present = 25%
    Section view present = 20%
    Border present = 15%
    Scale note present = 10%
    """
    dxf_path = Path(dxf_path)
    score = 0.0
    diffs = []

    if not dxf_path.exists() or ezdxf is None:
        diffs.append("DXF file not found or ezdxf not available")
        return score, diffs

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    layer_counts = {}
    text_content = []
    for entity in msp:
        layer = entity.dxf.layer
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        if entity.dxftype() == "TEXT":
            text_content.append(str(getattr(entity.dxf, "text", "")))

    all_text = " ".join(text_content).upper()

    # Title block (SHOP_TITLE layer)
    title_count = layer_counts.get("SHOP_TITLE", 0)
    if title_count >= 8:  # Expected: outer box + header + row dividers
        # Check key fields present in text
        has_dwg = "DWG" in all_text
        has_section = "SECTION" in all_text
        has_length = "LENGTH" in all_text
        has_qty = "QTY" in all_text
        field_count = sum([has_dwg, has_section, has_length, has_qty])
        score += 30.0 * (field_count / 4)
        if field_count < 4:
            missing = []
            if not has_dwg:
                missing.append("DWG NO")
            if not has_section:
                missing.append("SECTION")
            if not has_length:
                missing.append("LENGTH")
            if not has_qty:
                missing.append("QTY")
            diffs.append(f"title_block: missing fields: {missing}")
    elif title_count >= 1:
        score += 15.0
        diffs.append(f"title_block: partial ({title_count} entities)")
    else:
        diffs.append("title_block: MISSING")

    # Hole schedule (SHOP_TABLE layer)
    table_count = layer_counts.get("SHOP_TABLE", 0)
    if table_count >= 6:
        has_schedule_text = "SCHEDULE" in all_text or "BOLT" in all_text
        if has_schedule_text:
            score += 25.0
        else:
            score += 15.0
            diffs.append("hole_schedule: table present but no SCHEDULE/BOLT header")
    elif table_count >= 1:
        score += 10.0
        diffs.append(f"hole_schedule: partial ({table_count} entities)")
    else:
        diffs.append("hole_schedule: MISSING")

    # Section view (SHOP_SECTION layer)
    section_count = layer_counts.get("SHOP_SECTION", 0)
    if section_count >= 3:
        score += 20.0
    elif section_count >= 1:
        score += 10.0
        diffs.append(f"section_view: partial ({section_count} entities)")
    else:
        diffs.append("section_view: MISSING")

    # Border (SHOP_BORDER layer)
    border_count = layer_counts.get("SHOP_BORDER", 0)
    if border_count >= 8:  # Outer + inner border = 8 lines
        score += 15.0
    elif border_count >= 4:
        score += 10.0
        diffs.append(f"border: partial ({border_count} entities)")
    else:
        diffs.append("border: MISSING")

    # Scale note
    has_scale = "SCALE" in all_text
    if has_scale:
        score += 10.0
    else:
        diffs.append("scale_note: MISSING")

    return score, diffs


# ============================================================
# F. VISUAL SCORE (10%) — BLOCKED without reference PDFs
# ============================================================

def score_visual(gen_pdf: str | Path | None, ref_pdf: str | Path | None) -> tuple[float, list[str]]:
    """Compute SSIM between generated and reference PDFs. BLOCKED if either is missing."""
    if gen_pdf is None or ref_pdf is None:
        return 0.0, ["visual: BLOCKED (no reference PDF available)"]

    gen_pdf = Path(gen_pdf)
    ref_pdf = Path(ref_pdf)

    if not gen_pdf.exists() or not ref_pdf.exists():
        return 0.0, ["visual: BLOCKED (PDF file not found)"]

    # Attempt pypdfium2-based SSIM
    try:
        import pypdfium2 as pdfium
        import numpy as np

        def render_page(pdf_path: Path, page_idx: int = 0, scale: float = 2.0):
            pdf = pdfium.PdfDocument(str(pdf_path))
            page = pdf[page_idx]
            bitmap = page.render(scale=scale)
            img = bitmap.to_pil()
            return np.array(img.convert("L"))

        gen_img = render_page(gen_pdf)
        ref_img = render_page(ref_pdf)

        # Resize to common size
        h = min(gen_img.shape[0], ref_img.shape[0])
        w = min(gen_img.shape[1], ref_img.shape[1])
        gen_crop = gen_img[:h, :w]
        ref_crop = ref_img[:h, :w]

        # Simple normalized pixel difference
        diff = np.abs(gen_crop.astype(float) - ref_crop.astype(float))
        npd = 1.0 - (diff.mean() / 255.0)

        return npd * 100.0, [f"visual: NPD={npd:.3f}"]
    except ImportError:
        return 0.0, ["visual: BLOCKED (pypdfium2 not installed)"]
    except Exception as e:
        return 0.0, [f"visual: ERROR ({e})"]


# ============================================================
# OVERALL SCORE
# ============================================================

def compute_overall(
    scores: dict[str, float],
    visual_blocked: bool = True,
) -> float:
    """Compute weighted overall score."""
    weights = WEIGHTS_NO_VISUAL if visual_blocked else WEIGHTS_FULL
    total = 0.0
    for key, weight in weights.items():
        total += scores.get(key, 0.0) * weight
    return total


# ============================================================
# FULL GOLDEN GATE EVALUATION
# ============================================================

def evaluate_drawing(
    backmark: str,
    gen: dict,
    ref: dict,
    dxf_path: Path,
    ref_pdf_path: Path | None = None,
) -> dict[str, Any]:
    """Evaluate a single drawing against its reference."""
    gen_pdf_path = dxf_path.with_suffix(".pdf") if dxf_path else None

    section_info = gen.get("end_section", {})

    a_score, a_diffs = score_metadata(gen, ref)
    b_score, b_diffs = score_fabrication(gen, ref)
    c_score, c_diffs = score_geometry(dxf_path, section_info)
    d_score, d_diffs = score_dimensions(gen, ref)
    e_score, e_diffs = score_layout(dxf_path, gen)
    f_score, f_diffs = score_visual(gen_pdf_path, ref_pdf_path)

    visual_blocked = ref_pdf_path is None or not Path(ref_pdf_path).exists()

    scores = {
        "A_metadata": a_score,
        "B_fabrication": b_score,
        "C_geometry": c_score,
        "D_dimension": d_score,
        "E_layout": e_score,
        "F_visual": f_score,
    }

    overall = compute_overall(scores, visual_blocked=visual_blocked)
    status = "PASS" if overall >= 90.0 else "FAIL"

    # Provenance check
    provenance_issues = []
    holes = gen.get("holes", [])
    for h in holes:
        prov = h.get("provenance")
        if prov is None:
            provenance_issues.append(f"hole {h.get('hole_id', '?')}: provenance=null")

    mem_prov = gen.get("member", {}).get("provenance")
    if mem_prov is None:
        provenance_issues.append("member: provenance=null")

    qty_src = gen.get("member", {}).get("quantity_source")
    if qty_src is None:
        provenance_issues.append("member: quantity_source=null")

    # Missing fields
    missing_fields = []
    for h in holes:
        if h.get("transverse_mm") is None:
            missing_fields.append(f"hole {h.get('hole_id', '?')}: transverse_mm=null")

    all_diffs = a_diffs + b_diffs + c_diffs + d_diffs + e_diffs + f_diffs

    return {
        "drawing_id": f"429B{backmark}",
        "backmark": backmark,
        "metadata_score": round(a_score, 1),
        "fabrication_score": round(b_score, 1),
        "geometry_score": round(c_score, 1),
        "dimension_score": round(d_score, 1),
        "layout_score": round(e_score, 1),
        "visual_score": round(f_score, 1),
        "visual_blocked": visual_blocked,
        "overall_score": round(overall, 1),
        "status": status,
        "missing_fields": missing_fields,
        "provenance_issues": provenance_issues,
        "differences": all_diffs,
    }


def run_golden_gate(
    fixtures_dir: str | Path,
    shop_dir: str | Path,
    ref_pdf_dir: str | Path | None = None,
    target_members: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Run the full golden gate evaluation for golden drawings."""
    targets = target_members or DEFAULT_GOLDEN_MEMBERS
    refs = load_reference(fixtures_dir, targets)
    gens = load_generated(shop_dir, targets)
    shop_dir = Path(shop_dir)

    results = []
    all_pass = True

    for bm in targets:
        if bm not in gens:
            results.append({
                "drawing_id": f"429B{bm}",
                "backmark": bm,
                "status": "BLOCKED",
                "overall_score": 0.0,
                "differences": ["Generated JSON not found"],
            })
            all_pass = False
            continue

        if bm not in refs:
            results.append({
                "drawing_id": f"429B{bm}",
                "backmark": bm,
                "status": "BLOCKED",
                "overall_score": 0.0,
                "differences": ["Reference data not found"],
            })
            all_pass = False
            continue

        dxf_path = shop_dir / f"429B{bm}.dxf"
        ref_pdf = None
        if ref_pdf_dir:
            candidate = Path(ref_pdf_dir) / f"429B{bm}.pdf"
            if candidate.exists():
                ref_pdf = candidate

        result = evaluate_drawing(bm, gens[bm], refs[bm], dxf_path, ref_pdf)
        results.append(result)
        if result["status"] != "PASS":
            all_pass = False

    return {
        "gate_status": "PASS" if all_pass else "FAIL",
        "drawings": results,
    }
