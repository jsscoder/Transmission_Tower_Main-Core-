"""
FINAL SHOP DRAWING GENERATOR & CAD DETAILING ENGINE

Pipeline:

approved design JSON
        |
        v
canonical shop drawing model
        |
        +----> generated JSON sidecar
        |
        +----> editable CAD DXF (IS 802 standard)
        |
        +----> headless vector PDF
"""

from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from collections import Counter
from typing import Any

import ezdxf
from ezdxf.enums import TextEntityAlignment

from design_rules import (
    load_rules,
    validate_pattern,
    expand_pitch_pattern,
    get_gauge_distance,
    get_gauge_distances,
    DesignRuleError,
)
from domain.models import (
    build_dimension_chains,
    Hole,
    Member,
    ProvenanceRecord,
    TitleBlockData,
    ShopDrawingModel,
)
from domain.enums import SourceKind


# ============================================================
# CAD LAYERS (IS 802 Standard Specification)
# ============================================================

LAYER_MEMBER = "SHOP_MEMBER"
LAYER_HOLE = "SHOP_HOLE"
LAYER_CENTER = "SHOP_CENTER"
LAYER_DIM = "SHOP_DIM"
LAYER_TEXT = "SHOP_TEXT"
LAYER_SECTION = "SHOP_SECTION"
LAYER_BORDER = "SHOP_BORDER"
LAYER_TITLE = "SHOP_TITLE"
LAYER_TABLE = "SHOP_TABLE"


def _layers(doc: ezdxf.document.Drawing) -> None:
    """Ensure all canonical shop drawing layers exist in the DXF document."""
    layers = [
        (LAYER_MEMBER, 7),   # White / Black
        (LAYER_HOLE, 1),     # Red
        (LAYER_CENTER, 4),   # Cyan
        (LAYER_DIM, 3),      # Green
        (LAYER_TEXT, 2),     # Yellow
        (LAYER_SECTION, 5),  # Blue
        (LAYER_BORDER, 8),   # Dark Gray
        (LAYER_TITLE, 6),    # Magenta
        (LAYER_TABLE, 7),    # White / Table lines
    ]

    for name, color in layers:
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    if "DASHED" not in doc.linetypes:
        doc.linetypes.add("DASHED", pattern=[4.5, 3.0, -1.5], description="Dashed line")


# ============================================================
# BASIC CAD HELPERS
# ============================================================

def _text(
    msp,
    text: Any,
    position: tuple[float, float],
    height: float = 8.0,
    layer: str = LAYER_TEXT,
    align: TextEntityAlignment = TextEntityAlignment.LEFT,
):
    entity = msp.add_text(
        str(text),
        dxfattribs={
            "height": height,
            "layer": layer,
        },
    )
    entity.set_placement(position, align=align)
    return entity


def _line(
    msp,
    a: tuple[float, float],
    b: tuple[float, float],
    layer: str = LAYER_MEMBER,
    linetype: str | None = None,
):
    attribs = {"layer": layer}
    if linetype:
        attribs["linetype"] = linetype
    return msp.add_line(
        a,
        b,
        dxfattribs=attribs,
    )


def _circle(
    msp,
    center: tuple[float, float],
    radius: float,
    layer: str = LAYER_HOLE,
):
    return msp.add_circle(
        center,
        radius,
        dxfattribs={
            "layer": layer,
        },
    )


def _draw_hole_symbol(
    msp,
    center: tuple[float, float],
    dia: float,
    scale: float = 1.0,
):
    """Draw hole entity with symbol convention:
    - Dia 11.5: Open circle
    - Dia 13.5: Solid filled black circle (ezdxf solid hatch)
    - Dia 17.5: Open circle with crosshairs
    """
    hx, hy = center
    r = max(dia * scale / 2.0, 1.2)
    _circle(msp, (hx, hy), r, LAYER_HOLE)

    if abs(dia - 13.5) < 0.6:
        # Solid hatch fill for Dia 13.5 holes
        hatch = msp.add_hatch(color=7, dxfattribs={"layer": LAYER_HOLE})
        hatch.set_solid_fill()
        pts = [
            (hx + r * math.cos(2.0 * math.pi * i / 24.0), hy + r * math.sin(2.0 * math.pi * i / 24.0))
            for i in range(24)
        ]
        hatch.paths.add_polyline_path(pts, is_closed=True)
    elif abs(dia - 17.5) < 0.6:
        # Crosshairs inside circle
        _line(msp, (hx - r, hy), (hx + r, hy), LAYER_HOLE)
        _line(msp, (hx, hy - r), (hx, hy + r), LAYER_HOLE)

    # Centerlines
    cm = max(r * 1.4, 2.5)
    _line(msp, (hx - cm, hy), (hx + cm, hy), LAYER_CENTER)
    _line(msp, (hx, hy - cm), (hx, hy + cm), LAYER_CENTER)


# ============================================================
# SECTION PARSER
# ============================================================

def _parse_section(section: Any) -> dict[str, Any]:
    """Parse raw structural section string into structured dimensions."""
    if not section:
        return {"kind": "UNKNOWN"}

    raw = str(section).strip()
    s = raw.upper().replace(" ", "")

    # Flat bar with explicit THK: e.g. 4 thk x45, 6 thk x99, HT8 thk x154
    m_thk = re.search(r"(?:HT)?(\d+(?:\.\d+)?)THKX(\d+(?:\.\d+)?)", s)
    if m_thk:
        return {
            "kind": "FLAT",
            "name": raw,
            "thickness_mm": float(m_thk.group(1)),
            "width_mm": float(m_thk.group(2)),
        }

    # Flat bar with FLAT or PL: e.g. FLAT 4x45, PL 8x150, FLAT4X45
    if "FLAT" in s or s.startswith("PL"):
        m_flat = re.search(r"(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)", s)
        if m_flat:
            dim1 = float(m_flat.group(1))
            dim2 = float(m_flat.group(2))
            thk = min(dim1, dim2)
            w = max(dim1, dim2)
            return {
                "kind": "FLAT",
                "name": raw,
                "thickness_mm": thk,
                "width_mm": w,
            }

    # Angle with 3 dimensions: e.g. L50x50x5, HTL45x45x5, HT 50x50x5, ISA 65x65x6, 50x50x5
    m_angle3 = re.search(r"^(?:ISA|HTL|HT|L)?(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)", s)
    if m_angle3 and not ("THK" in s or "FLAT" in s):
        return {
            "kind": "ANGLE",
            "name": raw,
            "leg_a_mm": float(m_angle3.group(1)),
            "leg_b_mm": float(m_angle3.group(2)),
            "thickness_mm": float(m_angle3.group(3)),
        }

    # Angle with 2 dimensions: e.g. L50x50, HTL45x45, ISA 65x65
    m_angle2 = re.search(r"^(?:ISA|HTL|HT|L)(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)", s)
    if m_angle2 and not ("THK" in s or "FLAT" in s):
        return {
            "kind": "ANGLE",
            "name": raw,
            "leg_a_mm": float(m_angle2.group(1)),
            "leg_b_mm": float(m_angle2.group(2)),
            "thickness_mm": 0.0,
        }

    # Bare 2 dimensions "4X45" - flat if dim1 <= 16 and dim2 > dim1
    m_2dim = re.search(r"^(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)$", s)
    if m_2dim:
        d1 = float(m_2dim.group(1))
        d2 = float(m_2dim.group(2))
        if d1 <= 16.0 and d2 > d1:
            return {
                "kind": "FLAT",
                "name": raw,
                "thickness_mm": d1,
                "width_mm": d2,
            }
        else:
            return {
                "kind": "ANGLE",
                "name": raw,
                "leg_a_mm": d1,
                "leg_b_mm": d2,
                "thickness_mm": 0.0,
            }

    return {"kind": "UNKNOWN", "name": raw}


# ============================================================
# AUTO-SCALE ENGINE
# ============================================================

STANDARD_SCALES: list[tuple[float, str]] = [
    (1.0, "1:1"),
    (0.5, "1:2"),
    (0.2, "1:5"),
    (0.1, "1:10"),
    (1.0 / 15.0, "1:15"),
    (0.05, "1:20"),
    (0.04, "1:25"),
]


def select_drawing_scale(
    length_mm: float,
    max_width_mm: float = 330.0,
    height_mm: float = 0.0,
    max_height_mm: float = 70.0,
) -> tuple[float, str]:
    """Select the largest standard drawing scale so the member profile fits within max_width_mm and max_height_mm."""
    length = float(length_mm)
    if length <= 0:
        return (1.0, "1:1")

    h = float(height_mm) if height_mm is not None else 0.0

    for factor, label in STANDARD_SCALES:
        if length * factor <= max_width_mm and (h <= 0 or h * factor <= max_height_mm):
            return (factor, label)

    return (1.0 / 50.0, "1:50")


# ============================================================
# HOLE NORMALIZATION
# ============================================================

def _normalise_hole(hole: Any, end_name: str) -> dict[str, Any]:
    if hasattr(hole, "to_dict"):
        hole = hole.to_dict()

    position = float(hole.get("along_mm") if isinstance(hole, dict) else hole.along_mm)

    raw_dia = hole.get("hole_diameter_mm") if isinstance(hole, dict) else getattr(hole, "diameter_mm", None)
    if raw_dia is None and isinstance(hole, dict):
        raw_dia = hole.get("diameter_mm")
    diameter = float(raw_dia if raw_dia is not None else 0.0)

    bolt = hole.get("nominal_bolt_diameter_mm") if isinstance(hole, dict) else getattr(hole, "nominal_bolt_diameter_mm", None)
    if bolt is not None:
        bolt = float(bolt)

    edge = hole.get("edge_distance_mm") if isinstance(hole, dict) else getattr(hole, "edge_distance_mm", None)
    if edge is not None:
        edge = float(edge)

    trans = hole.get("transverse_mm") if isinstance(hole, dict) else getattr(hole, "transverse_mm", None)
    if trans is not None:
        trans = float(trans)

    return {
        "end": str(end_name),
        "along_mm": position,
        "transverse_mm": trans,
        "hole_diameter_mm": diameter,
        "diameter_mm": diameter,
        "nominal_bolt_diameter_mm": bolt,
        "edge_distance_mm": edge,
    }


def _expand_end_holes(end_name: str, end: Any) -> list[dict[str, Any]]:
    holes = []
    if not isinstance(end, dict):
        return holes

    # Explicit holes
    for raw in end.get("holes", []):
        holes.append(_normalise_hole(raw, end_name))

    # Pattern definition
    pattern = end.get("pattern")
    if pattern:
        count = int(pattern["count"])
        start = float(pattern["start_from_end_mm"])
        pitch = float(pattern["pitch_mm"])
        diameter = float(pattern["hole_diameter_mm"])
        bolt = pattern.get("nominal_bolt_diameter_mm")
        edge = pattern.get("edge_distance_mm")
        trans = pattern.get("transverse_mm")

        for i in range(count):
            holes.append({
                "end": str(end_name),
                "along_mm": start + i * pitch,
                "transverse_mm": float(trans) if trans is not None else None,
                "hole_diameter_mm": diameter,
                "diameter_mm": diameter,
                "nominal_bolt_diameter_mm": float(bolt) if bolt is not None else None,
                "edge_distance_mm": float(edge) if edge is not None else None,
            })

    holes.sort(key=lambda x: x["along_mm"])
    return holes


# ============================================================
# VALIDATE MEMBER DESIGN
# ============================================================

def validate_member_design(member: Any, rules: dict[str, Any]) -> list[dict[str, Any]]:
    if hasattr(member, "to_dict"):
        member = member.to_dict()

    required = ["backmark", "section", "length_mm", "ends"]
    missing = [key for key in required if key not in member]
    if missing:
        raise ValueError(f"Member {member.get('backmark')} missing fields: {missing}")

    raw_qty = member.get("qty")
    if raw_qty is None:
        raw_qty = member.get("quantity")
    if raw_qty is None:
        raise ValueError(f"Member {member.get('backmark')} missing quantity")
    try:
        qty = int(raw_qty)
        if qty <= 0:
            raise ValueError(f"{member['backmark']}: invalid quantity {raw_qty}")
    except (TypeError, ValueError):
        raise ValueError(f"{member['backmark']}: invalid quantity {raw_qty}")

    length = float(member["length_mm"])
    if length <= 0:
        raise ValueError(f"{member['backmark']}: invalid length")

    all_holes = []
    for end_name, end in sorted(member["ends"].items()):
        holes = _expand_end_holes(end_name, end)
        if not holes:
            continue

        result = validate_pattern(rules, length, holes)
        if not result["valid"]:
            raise DesignRuleError(f"{member['backmark']} {end_name}: " + "; ".join(result["errors"]))

        all_holes.extend(holes)

    if not all_holes:
        raise ValueError(f"{member.get('backmark')}: no fabrication holes defined (MISSING_FABRICATION_HOLES)")

    return all_holes


# ============================================================
# MEMBER RENDERERS (True CAD Profiles)
# ============================================================

class AngleMemberRenderer:
    """Renders angle elevation showing leg width, dashed thickness line (t), heel, 45° bevel cuts, and hole centers."""

    def __init__(
        self,
        leg_a_mm: float,
        leg_b_mm: float,
        thickness_mm: float,
        length_mm: float,
        gauge_mm: float | tuple[float, ...] | list[float] | None = None,
        miter_cut: bool = True,
    ):
        self.leg_a_mm = float(leg_a_mm)
        self.leg_b_mm = float(leg_b_mm)
        self.thickness_mm = float(thickness_mm)
        self.length_mm = float(length_mm)
        self.miter_cut = miter_cut
        if isinstance(gauge_mm, (list, tuple)):
            self.gauge_lines = [float(g) for g in gauge_mm if g is not None and float(g) > 0]
            self.gauge_mm = self.gauge_lines[0] if self.gauge_lines else 25.0
        elif gauge_mm is not None and float(gauge_mm) > 0:
            self.gauge_lines = [float(gauge_mm)]
            self.gauge_mm = float(gauge_mm)
        else:
            self.gauge_lines = [25.0]
            self.gauge_mm = 25.0

    def render(
        self,
        msp,
        origin: tuple[float, float],
        holes: list[dict[str, Any] | Hole],
        scale: float = 1.0,
    ) -> dict[str, Any]:
        x0, y0 = origin
        ls = self.length_mm * scale
        ws = self.leg_b_mm * scale
        ts = max(self.thickness_mm * scale, 0.6)

        if self.miter_cut:
            bevel = ws
            # Heel line (bottom outer edge)
            _line(msp, (x0, y0), (x0 + ls, y0), LAYER_MEMBER)
            # Toe line (top edge, shortened by bevel at both ends)
            _line(msp, (x0 + bevel, y0 + ws), (x0 + ls - bevel, y0 + ws), LAYER_MEMBER)
            # Left 45° bevel cut
            _line(msp, (x0, y0), (x0 + bevel, y0 + ws), LAYER_MEMBER)
            # Right 45° bevel cut
            _line(msp, (x0 + ls, y0), (x0 + ls - bevel, y0 + ws), LAYER_MEMBER)
            # Flange thickness line (dashed)
            _line(msp, (x0 + ts, y0 + ts), (x0 + ls - ts, y0 + ts), LAYER_MEMBER, linetype="DASHED")

            # 45° annotations and arrows
            mx_l = x0 + bevel / 2.0
            my_l = y0 + ws / 2.0
            _line(msp, (mx_l - 6.0, my_l + 6.0), (mx_l, my_l), LAYER_DIM)
            _line(msp, (mx_l, my_l), (mx_l - 1.2, my_l + 2.0), LAYER_DIM)
            _line(msp, (mx_l, my_l), (mx_l - 2.0, my_l + 1.2), LAYER_DIM)
            ent_l = msp.add_text("45°", dxfattribs={"height": 2.2, "layer": LAYER_DIM})
            ent_l.set_placement((mx_l - 7.0, my_l + 7.0), align=TextEntityAlignment.MIDDLE_RIGHT)

            mx_r = x0 + ls - bevel / 2.0
            my_r = y0 + ws / 2.0
            _line(msp, (mx_r + 6.0, my_r + 6.0), (mx_r, my_r), LAYER_DIM)
            _line(msp, (mx_r, my_r), (mx_r + 1.2, my_r + 2.0), LAYER_DIM)
            _line(msp, (mx_r, my_r), (mx_r + 2.0, my_r + 1.2), LAYER_DIM)
            ent_r = msp.add_text("45°", dxfattribs={"height": 2.2, "layer": LAYER_DIM})
            ent_r.set_placement((mx_r + 7.0, my_r + 7.0), align=TextEntityAlignment.MIDDLE_LEFT)
        else:
            # Square end cuts
            _line(msp, (x0, y0), (x0 + ls, y0), LAYER_MEMBER)
            _line(msp, (x0, y0 + ws), (x0 + ls, y0 + ws), LAYER_MEMBER)
            _line(msp, (x0, y0), (x0, y0 + ws), LAYER_MEMBER)
            _line(msp, (x0 + ls, y0), (x0 + ls, y0 + ws), LAYER_MEMBER)
            _line(msp, (x0, y0 + ts), (x0 + ls, y0 + ts), LAYER_MEMBER, linetype="DASHED")

        # Draw centerlines for all gauge lines
        all_gauge_lines = set(self.gauge_lines)
        for h in holes:
            trans = h.get("transverse_mm") if isinstance(h, dict) else getattr(h, "transverse_mm", None)
            if trans is None:
                trans = h.get("edge_distance_mm") if isinstance(h, dict) else getattr(h, "edge_distance_mm", None)
            if trans is not None and float(trans) > 0:
                all_gauge_lines.add(float(trans))

        for g_val in sorted(all_gauge_lines):
            gs = g_val * scale
            _line(msp, (x0 - 5.0, y0 + gs), (x0 + ls + 5.0, y0 + gs), LAYER_CENTER)

        # Holes
        primary_gauge = self.gauge_lines[0] if self.gauge_lines else 25.0
        for h in holes:
            pos = float(h.get("along_mm") if isinstance(h, dict) else h.along_mm)
            hx = x0 + pos * scale
            trans = h.get("transverse_mm") if isinstance(h, dict) else getattr(h, "transverse_mm", None)
            if trans is None:
                trans = h.get("edge_distance_mm") if isinstance(h, dict) else getattr(h, "edge_distance_mm", None)
            hy = y0 + float(trans) * scale if trans is not None else y0 + primary_gauge * scale
            dia = float(h.get("hole_diameter_mm", h.get("diameter_mm", 13.5)) if isinstance(h, dict) else getattr(h, "diameter_mm", 13.5))
            _draw_hole_symbol(msp, (hx, hy), dia, scale)

        # Transverse gauge dimension at left end
        g_val = self.gauge_mm or 25.0
        x_gdim = x0 - 10.0
        y_heel = y0
        y_g = y0 + g_val * scale
        _line(msp, (x0, y_heel), (x_gdim - 2.0, y_heel), LAYER_DIM)
        _line(msp, (x0, y_g), (x_gdim - 2.0, y_g), LAYER_DIM)
        _line(msp, (x_gdim, y_heel), (x_gdim, y_g), LAYER_DIM)
        _line(msp, (x_gdim - 0.8, y_heel - 0.8), (x_gdim + 0.8, y_heel + 0.8), LAYER_DIM)
        _line(msp, (x_gdim - 0.8, y_g - 0.8), (x_gdim + 0.8, y_g + 0.8), LAYER_DIM)
        ent_g = msp.add_text(f"{g_val:g}", dxfattribs={"height": 2.2, "layer": LAYER_DIM})
        ent_g.set_placement((x_gdim - 1.5, (y_heel + y_g) / 2.0), align=TextEntityAlignment.MIDDLE_RIGHT)

        return {
            "kind": "ANGLE",
            "leg_a_mm": self.leg_a_mm,
            "leg_b_mm": self.leg_b_mm,
            "thickness_mm": self.thickness_mm,
            "length_mm": self.length_mm,
            "gauge_mm": self.gauge_mm,
            "gauge_lines": self.gauge_lines,
            "scaled_length": ls,
            "scaled_width": ws,
        }


class FlatMemberRenderer:
    """Renders rectangular flat bar outline (w x L) with closed boundary, centerline, and transverse end dimensions."""

    def __init__(
        self,
        width_mm: float,
        thickness_mm: float,
        length_mm: float,
    ):
        self.width_mm = float(width_mm)
        self.thickness_mm = float(thickness_mm)
        self.length_mm = float(length_mm)

    def render(
        self,
        msp,
        origin: tuple[float, float],
        holes: list[dict[str, Any] | Hole],
        scale: float = 1.0,
    ) -> dict[str, Any]:
        x0, y0 = origin
        ls = self.length_mm * scale
        ws = self.width_mm * scale
        hw = ws / 2.0
        y_bot = y0 - hw
        y_top = y0 + hw

        # Closed boundary (4 lines on LAYER_MEMBER)
        _line(msp, (x0, y_bot), (x0 + ls, y_bot), LAYER_MEMBER)
        _line(msp, (x0, y_top), (x0 + ls, y_top), LAYER_MEMBER)
        _line(msp, (x0, y_bot), (x0, y_top), LAYER_MEMBER)
        _line(msp, (x0 + ls, y_bot), (x0 + ls, y_top), LAYER_MEMBER)

        # Centerlines along length for unique transverse gauge lines
        unique_trans = []
        for h in holes:
            t = h.get("transverse_mm") if isinstance(h, dict) else getattr(h, "transverse_mm", None)
            if t is None:
                t = h.get("edge_distance_mm") if isinstance(h, dict) else getattr(h, "edge_distance_mm", None)
            if t is not None and float(t) not in unique_trans:
                unique_trans.append(float(t))
        if not unique_trans:
            unique_trans = [22.0]
        unique_trans.sort()

        for t_val in unique_trans:
            y_hl = y_bot + t_val * scale
            _line(msp, (x0 - 5.0, y_hl), (x0 + ls + 5.0, y_hl), LAYER_CENTER)

        # Holes
        for h in holes:
            pos = float(h.get("along_mm") if isinstance(h, dict) else h.along_mm)
            hx = x0 + pos * scale
            trans = h.get("transverse_mm") if isinstance(h, dict) else getattr(h, "transverse_mm", None)
            if trans is None:
                trans = h.get("edge_distance_mm") if isinstance(h, dict) else getattr(h, "edge_distance_mm", None)
            hy = y_bot + float(trans) * scale if trans is not None else (y_bot + unique_trans[0] * scale)
            dia = float(h.get("hole_diameter_mm", h.get("diameter_mm", 13.5)) if isinstance(h, dict) else getattr(h, "diameter_mm", 13.5))
            _draw_hole_symbol(msp, (hx, hy), dia, scale)

        # Transverse end dimensions at left end
        trans_points = [0.0] + unique_trans + [self.width_mm]
        x1 = x0 - 8.0
        x2 = x0 - 18.0

        # Witness lines at x1
        for tp in trans_points:
            yp = y_bot + tp * scale
            _line(msp, (x0, yp), (x1 - 2.0, yp), LAYER_DIM)
            _line(msp, (x1 - 0.8, yp - 0.8), (x1 + 0.8, yp + 0.8), LAYER_DIM)

        # Dimension segments and text
        for a, b in zip(trans_points, trans_points[1:]):
            ya = y_bot + a * scale
            yb = y_bot + b * scale
            _line(msp, (x1, ya), (x1, yb), LAYER_DIM)
            mid_y = (ya + yb) / 2.0
            val = round(b - a, 2)
            ent_s = msp.add_text(f"{val:g}", dxfattribs={"height": 2.2, "layer": LAYER_DIM})
            ent_s.set_placement((x1 - 1.2, mid_y), align=TextEntityAlignment.MIDDLE_RIGHT)

        # Outer total width dimension line at x2
        _line(msp, (x1 - 2.0, y_bot), (x2 - 2.0, y_bot), LAYER_DIM)
        _line(msp, (x1 - 2.0, y_top), (x2 - 2.0, y_top), LAYER_DIM)
        _line(msp, (x2, y_bot), (x2, y_top), LAYER_DIM)
        _line(msp, (x2 - 0.8, y_bot - 0.8), (x2 + 0.8, y_bot + 0.8), LAYER_DIM)
        _line(msp, (x2 - 0.8, y_top - 0.8), (x2 + 0.8, y_top + 0.8), LAYER_DIM)

        ent_w = msp.add_text(f"{round(self.width_mm):g}", dxfattribs={"height": 2.4, "layer": LAYER_DIM})
        ent_w.set_placement((x2 - 1.2, (y_bot + y_top) / 2.0), align=TextEntityAlignment.MIDDLE_RIGHT)

        return {
            "kind": "FLAT",
            "width_mm": self.width_mm,
            "thickness_mm": self.thickness_mm,
            "length_mm": self.length_mm,
            "scaled_length": ls,
            "scaled_width": ws,
        }


class EndSectionRenderer:
    """True cross-section end view: L-shaped polygon for angles, rectangular for flats."""

    def __init__(self, section_info: dict[str, Any]):
        self.section_info = dict(section_info)

    def render(
        self,
        msp,
        origin: tuple[float, float],
        scale: float = 1.0,
    ) -> dict[str, Any]:
        xs, ys = origin
        kind = self.section_info.get("kind", "UNKNOWN")

        if kind == "ANGLE":
            a = float(self.section_info.get("leg_a_mm") or 50.0) * scale
            b = float(self.section_info.get("leg_b_mm") or 50.0) * scale
            raw_t = float(self.section_info.get("thickness_mm") or 0.0)
            t = max(raw_t * scale, 1.2) if raw_t > 0 else max(5.0 * scale, 1.2)

            pts = [
                (xs, ys),
                (xs + a, ys),
                (xs + a, ys + t),
                (xs + t, ys + t),
                (xs + t, ys + b),
                (xs, ys + b),
            ]
            for i in range(len(pts)):
                p_cur = pts[i]
                p_next = pts[(i + 1) % len(pts)]
                _line(msp, p_cur, p_next, LAYER_SECTION)

            _text(msp, "END SECTION", (xs, ys + b + 6.0), height=3.2, layer=LAYER_TEXT)
            la = float(self.section_info.get("leg_a_mm") or 0.0)
            lb = float(self.section_info.get("leg_b_mm") or 0.0)
            if raw_t > 0:
                label = f"L{la:g}x{lb:g}x{raw_t:g}"
            else:
                label = f"L{la:g}x{lb:g}"
            _text(msp, label, (xs, ys - 7.0), height=2.8, layer=LAYER_TEXT)

        elif kind == "FLAT":
            w = float(self.section_info.get("width_mm") or 45.0) * scale
            raw_t = float(self.section_info.get("thickness_mm") or 4.0)
            t = max(raw_t * scale, 1.5)

            pts = [
                (xs, ys - w / 2.0),
                (xs + t, ys - w / 2.0),
                (xs + t, ys + w / 2.0),
                (xs, ys + w / 2.0),
            ]
            for i in range(len(pts)):
                p_cur = pts[i]
                p_next = pts[(i + 1) % len(pts)]
                _line(msp, p_cur, p_next, LAYER_SECTION)

            _text(msp, "END SECTION", (xs, ys + w / 2.0 + 6.0), height=3.2, layer=LAYER_TEXT)
            label = f"FLAT {raw_t:g}x{float(self.section_info.get('width_mm') or 0.0):g}"
            _text(msp, label, (xs, ys - w / 2.0 - 7.0), height=2.8, layer=LAYER_TEXT)

        return self.section_info


# ============================================================
# MULTI-TIER DIMENSION ENGINE
# ============================================================

class MultiTierDimensionRenderer:
    """Renders structured multi-tier dimension chains: Level 1 (holes), Level 2 (overall), Level 3 (transverse gauge)."""

    def __init__(
        self,
        length_mm: float,
        holes: list[dict[str, Any] | Hole],
        gauge_mm: float | tuple[float, ...] | list[float] | None = None,
    ):
        self.length_mm = float(length_mm)
        self.holes = holes
        if isinstance(gauge_mm, (list, tuple)):
            self.gauge_lines = [float(g) for g in gauge_mm if g is not None and float(g) > 0]
            self.gauge_mm = self.gauge_lines[0] if self.gauge_lines else None
        elif gauge_mm is not None and float(gauge_mm) > 0:
            self.gauge_lines = [float(gauge_mm)]
            self.gauge_mm = float(gauge_mm)
        else:
            self.gauge_lines = []
            self.gauge_mm = None

    def render(
        self,
        msp,
        origin: tuple[float, float],
        scale: float = 1.0,
        dim_y_level1: float = 175.0,
        dim_y_level2: float = 158.0,
        member_bottom_y: float = 205.0,
    ):
        x0, _ = origin
        ls = self.length_mm * scale

        raw_positions = sorted(set(
            float(h.get("along_mm") if isinstance(h, dict) else h.along_mm)
            for h in self.holes
        ))

        all_points = [0.0] + raw_positions + [self.length_mm]

        # ----------------------------------------------------
        # Level 1: Hole incremental chain
        # ----------------------------------------------------
        y1 = dim_y_level1
        for pt in all_points:
            px = x0 + pt * scale
            _line(msp, (px, member_bottom_y - 2.0), (px, y1 - 2.5), LAYER_DIM)
            _line(msp, (px - 0.8, y1 - 0.8), (px + 0.8, y1 + 0.8), LAYER_DIM)

        for a, b in zip(all_points, all_points[1:]):
            delta = b - a
            xa = x0 + a * scale
            xb = x0 + b * scale
            _line(msp, (xa, y1), (xb, y1), LAYER_DIM)
            mid_x = (xa + xb) / 2.0
            label = f"{round(delta, 2):g}"
            ent = msp.add_text(
                label,
                dxfattribs={"height": 2.2, "layer": LAYER_DIM},
            )
            ent.set_placement((mid_x, y1 + 1.2), align=TextEntityAlignment.MIDDLE_CENTER)

        # ----------------------------------------------------
        # Level 2: Overall length
        # ----------------------------------------------------
        y2 = dim_y_level2
        _line(msp, (x0, y1 - 2.5), (x0, y2 - 2.5), LAYER_DIM)
        _line(msp, (x0 + ls, y1 - 2.5), (x0 + ls, y2 - 2.5), LAYER_DIM)
        _line(msp, (x0, y2), (x0 + ls, y2), LAYER_DIM)
        _line(msp, (x0 - 1.0, y2 - 1.0), (x0 + 1.0, y2 + 1.0), LAYER_DIM)
        _line(msp, (x0 + ls - 1.0, y2 - 1.0), (x0 + ls + 1.0, y2 + 1.0), LAYER_DIM)

        ent = msp.add_text(
            f"OVERALL = {self.length_mm:g} mm",
            dxfattribs={"height": 2.8, "layer": LAYER_DIM},
        )
        ent.set_placement((x0 + ls / 2.0, y2 + 1.4), align=TextEntityAlignment.MIDDLE_CENTER)

        # ----------------------------------------------------
        # Level 3: Transverse gauge (if provided)
        # ----------------------------------------------------
        if self.gauge_lines:
            x_dim3 = max(x0 - 14.0, 32.0)
            y_heel = member_bottom_y

            # Witness lines and ticks for each gauge line
            for g_val in self.gauge_lines:
                y_gauge = y_heel + g_val * scale
                _line(msp, (x0 - 2.0, y_gauge), (x_dim3 - 2.0, y_gauge), LAYER_DIM)
                _line(msp, (x_dim3 - 0.8, y_gauge - 0.8), (x_dim3 + 0.8, y_gauge + 0.8), LAYER_DIM)

            # Witness line at heel
            _line(msp, (x0 - 2.0, y_heel), (x_dim3 - 2.0, y_heel), LAYER_DIM)
            _line(msp, (x_dim3 - 0.8, y_heel - 0.8), (x_dim3 + 0.8, y_heel + 0.8), LAYER_DIM)

            # Dimension line between heel and top gauge
            top_y_gauge = y_heel + self.gauge_lines[-1] * scale
            _line(msp, (x_dim3, y_heel), (x_dim3, top_y_gauge), LAYER_DIM)

            # Text labels
            if len(self.gauge_lines) == 1:
                g_val = self.gauge_lines[0]
                ent = msp.add_text(
                    f"GAUGE = {g_val:g} mm",
                    dxfattribs={"height": 2.2, "layer": LAYER_DIM},
                )
                ent.set_placement((x_dim3 - 2.0, (y_heel + y_heel + g_val * scale) / 2.0), align=TextEntityAlignment.MIDDLE_RIGHT)
            else:
                for idx, g_val in enumerate(self.gauge_lines):
                    prev_y = y_heel if idx == 0 else y_heel + self.gauge_lines[idx - 1] * scale
                    cur_y = y_heel + g_val * scale
                    ent = msp.add_text(
                        f"GAUGE {idx+1} = {g_val:g} mm",
                        dxfattribs={"height": 2.0, "layer": LAYER_DIM},
                    )
                    ent.set_placement((x_dim3 - 2.0, (prev_y + cur_y) / 2.0), align=TextEntityAlignment.MIDDLE_RIGHT)


# ============================================================
# TITLE BLOCK & HOLE SCHEDULE ENGINE
# ============================================================

class TitleBlockRenderer:
    """Reusable title block component conforming to tower manufacturing standards."""

    def __init__(self, data: TitleBlockData | dict[str, Any]):
        if hasattr(data, "to_dict"):
            self.data = data.to_dict()
        else:
            self.data = dict(data)

    def render(
        self,
        msp,
        origin: tuple[float, float] = (245.0, 15.0),
        width: float = 160.0,
        height: float = 55.0,
    ):
        x, y = origin
        # Outer box
        _line(msp, (x, y), (x + width, y), LAYER_TITLE)
        _line(msp, (x, y + height), (x + width, y + height), LAYER_TITLE)
        _line(msp, (x, y), (x, y + height), LAYER_TITLE)
        _line(msp, (x + width, y), (x + width, y + height), LAYER_TITLE)

        # Header banner
        _line(msp, (x, y + height - 10.0), (x + width, y + height - 10.0), LAYER_TITLE)
        ent = msp.add_text(
            "TRANSMISSION TOWER SHOP DRAWING",
            dxfattribs={"height": 3.2, "layer": LAYER_TEXT},
        )
        ent.set_placement((x + width / 2.0, y + height - 6.5), align=TextEntityAlignment.MIDDLE_CENTER)

        rows = [
            [("DWG NO", self.data.get("drawing_id", "")), ("REV", str(self.data.get("rev", "0"))), ("DATE", str(self.data.get("date", date.today().isoformat())))],
            [("BACKMARK", str(self.data.get("backmark", ""))), ("QTY", str(self.data.get("quantity") or self.data.get("qty", 1))), ("JOB", str(self.data.get("job_no") or self.data.get("job", "WO_429")))],
            [("SECTION", str(self.data.get("section", ""))), ("LENGTH", f"{float(self.data.get('length_mm', 0)):g} mm")],
            [("STANDARD", str(self.data.get("standard", "IS 802"))), ("STATUS", str(self.data.get("status", "FABRICATION READY")))],
        ]

        row_h = 9.0
        cur_y = y + height - 10.0
        for row in rows:
            ny = cur_y - row_h
            _line(msp, (x, ny), (x + width, ny), LAYER_TITLE)
            col_w = width / len(row)
            for c_idx, (k, v) in enumerate(row):
                cx = x + c_idx * col_w
                if c_idx > 0:
                    _line(msp, (cx, cur_y), (cx, ny), LAYER_TITLE)
                _text(msp, f"{k}:", (cx + 2.0, ny + 3.0), height=2.2, layer=LAYER_TEXT)
                _text(msp, f"{v}", (cx + 22.0, ny + 3.0), height=2.4, layer=LAYER_TEXT)
            cur_y = ny


class HoleScheduleRenderer:
    """Standard Bolt/Hole Schedule table showing Symbol, Hole Dia, Nominal Bolt, Qty/pc, Total Qty, and Location."""

    def __init__(self, holes: list[dict[str, Any] | Hole], member_qty: int = 1):
        self.holes = holes
        self.member_qty = max(int(member_qty if member_qty is not None else 1), 1)

    def render(
        self,
        msp,
        origin: tuple[float, float] = (245.0, 75.0),
        width: float = 160.0,
    ) -> int:
        groups: dict[tuple[float, str], list[dict]] = {}
        for h in self.holes:
            dia = float(h.get("hole_diameter_mm", h.get("diameter_mm", 0)) if isinstance(h, dict) else getattr(h, "diameter_mm", 0))
            bolt = h.get("nominal_bolt_diameter_mm") if isinstance(h, dict) else getattr(h, "nominal_bolt_diameter_mm", None)
            bolt_str = f"M{int(float(bolt))}" if bolt is not None else "-"
            end = str(h.get("end", "E1") if isinstance(h, dict) else getattr(h, "end", "E1"))
            key = (dia, bolt_str)
            groups.setdefault(key, []).append({"end": end})

        num_rows = max(len(groups), 1)
        row_h = 7.0
        hdr_h = 8.0
        title_h = 8.0
        total_h = title_h + hdr_h + num_rows * row_h

        x, y = origin
        top_y = y + total_h

        # Outer box
        _line(msp, (x, y), (x + width, y), LAYER_TABLE)
        _line(msp, (x, top_y), (x + width, top_y), LAYER_TABLE)
        _line(msp, (x, y), (x, top_y), LAYER_TABLE)
        _line(msp, (x + width, y), (x + width, top_y), LAYER_TABLE)

        # Title
        _line(msp, (x, top_y - title_h), (x + width, top_y - title_h), LAYER_TABLE)
        ent = msp.add_text(
            "BOLT / HOLE SCHEDULE",
            dxfattribs={"height": 2.8, "layer": LAYER_TEXT},
        )
        ent.set_placement((x + width / 2.0, top_y - 5.0), align=TextEntityAlignment.MIDDLE_CENTER)

        cols = [
            ("SYM", 18.0),
            ("HOLE Ø", 28.0),
            ("BOLT", 24.0),
            ("QTY/PC", 24.0),
            ("TOTAL", 24.0),
            ("LOC", 42.0),
        ]
        h_y = top_y - title_h
        b_y = h_y - hdr_h
        _line(msp, (x, b_y), (x + width, b_y), LAYER_TABLE)

        cur_x = x
        for col_name, col_w in cols:
            ent = msp.add_text(
                col_name,
                dxfattribs={"height": 2.2, "layer": LAYER_TEXT},
            )
            ent.set_placement((cur_x + col_w / 2.0, b_y + 2.5), align=TextEntityAlignment.MIDDLE_CENTER)
            cur_x += col_w
            if cur_x < x + width:
                _line(msp, (cur_x, h_y), (cur_x, y), LAYER_TABLE)

        symbols = ["A", "B", "C", "D", "E", "F", "G"]
        r_y = b_y
        if not groups:
            ny = r_y - row_h
            row_vals = ["-", "-", "-", "0", "0", "-"]
            cur_x = x
            for (v, (_, col_w)) in zip(row_vals, cols):
                ent = msp.add_text(
                    v,
                    dxfattribs={"height": 2.1, "layer": LAYER_TEXT},
                )
                ent.set_placement((cur_x + col_w / 2.0, ny + 2.2), align=TextEntityAlignment.MIDDLE_CENTER)
                cur_x += col_w
        else:
            for idx, ((dia, bolt_str), items) in enumerate(sorted(groups.items())):
                ny = r_y - row_h
                if idx < num_rows - 1:
                    _line(msp, (x, ny), (x + width, ny), LAYER_TABLE)
                qty_pc = len(items)
                total_qty = qty_pc * self.member_qty
                ends = sorted(set(it["end"] for it in items))
                loc_str = ", ".join(ends)
                sym = symbols[idx % len(symbols)]

                row_vals = [
                    sym,
                    f"Ø{dia:g}",
                    bolt_str,
                    str(qty_pc),
                    str(total_qty),
                    loc_str,
                ]
                cur_x = x
                for (v, (_, col_w)) in zip(row_vals, cols):
                    ent = msp.add_text(
                        v,
                        dxfattribs={"height": 2.1, "layer": LAYER_TEXT},
                    )
                    ent.set_placement((cur_x + col_w / 2.0, ny + 2.2), align=TextEntityAlignment.MIDDLE_CENTER)
                    cur_x += col_w
                r_y = ny

        return len(groups)


def _draw_sheet_frame(msp, width: float = 420.0, height: float = 297.0, margin: float = 10.0):
    """Draw standard drawing border frame and header."""
    # Outer sheet edge
    _line(msp, (0, 0), (width, 0), LAYER_BORDER)
    _line(msp, (width, 0), (width, height), LAYER_BORDER)
    _line(msp, (width, height), (0, height), LAYER_BORDER)
    _line(msp, (0, height), (0, 0), LAYER_BORDER)

    # Inner drawing margin
    _line(msp, (margin, margin), (width - margin, margin), LAYER_BORDER)
    _line(msp, (width - margin, margin), (width - margin, height - margin), LAYER_BORDER)
    _line(msp, (width - margin, height - margin), (margin, height - margin), LAYER_BORDER)
    _line(msp, (margin, height - margin), (margin, margin), LAYER_BORDER)

    # Drawing title
    _text(msp, "TRANSMISSION TOWER SHOP FABRICATION DRAWING", (margin + 5.0, height - margin - 8.0), height=4.5, layer=LAYER_TEXT)
    _text(msp, "STANDARD: IS 802 | ALL FABRICATION HOLES FROM APPROVED ENGINEERING INPUT", (margin + 5.0, height - margin - 15.0), height=2.4, layer=LAYER_TEXT)


# ============================================================
# BACKWARD COMPATIBLE DRAWING FUNCTIONS
# ============================================================

def _draw_member(msp, section, length, origin, scale=1.0):
    info = _parse_section(section)
    if info["kind"] == "ANGLE":
        renderer = AngleMemberRenderer(info.get("leg_a_mm", 50), info.get("leg_b_mm", 50), info.get("thickness_mm", 5), length)
        renderer.render(msp, origin, [], scale)
    elif info["kind"] == "FLAT":
        renderer = FlatMemberRenderer(info.get("width_mm", 45), info.get("thickness_mm", 4), length)
        renderer.render(msp, origin, [], scale)
    else:
        x0, y0 = origin
        x1 = x0 + length * scale
        _line(msp, (x0, y0), (x1, y0), LAYER_MEMBER)
    return info


def _draw_holes(msp, holes, origin, y, scale=1.0):
    x0, _ = origin
    for hole in holes:
        pos = float(hole["along_mm"] if isinstance(hole, dict) else hole.along_mm)
        x = x0 + pos * scale
        dia = float(hole.get("hole_diameter_mm", 13.5) if isinstance(hole, dict) else getattr(hole, "diameter_mm", 13.5))
        radius = max(dia * scale / 2.0, 1.5)
        _circle(msp, (x, y), radius, LAYER_HOLE)
        mark = max(radius * 1.5, 4.0)
        _line(msp, (x - mark, y), (x + mark, y), LAYER_CENTER)
        _line(msp, (x, y - mark), (x, y + mark), LAYER_CENTER)


def _draw_dimension_chain(msp, positions, y, base_x, scale=1.0, overall_length_mm=None):
    if overall_length_mm is None:
        raise ValueError(
            "overall_length_mm must be explicitly provided to _draw_dimension_chain; "
            "never synthesize overall length from hole positions."
        )
    overall = float(overall_length_mm)
    positions = sorted(float(x) for x in positions) if positions else []

    all_pts = [0.0] + positions + [overall]
    for pt in all_pts:
        x = base_x + pt * scale
        _line(msp, (x, y), (x, y + 20.0), LAYER_DIM)

    for a, b in zip(all_pts, all_pts[1:]):
        delta = b - a
        xa = base_x + a * scale
        xb = base_x + b * scale
        _line(msp, (xa, y + 10.0), (xb, y + 10.0), LAYER_DIM)
        _text(msp, f"{delta:g}", ((xa + xb) / 2.0, y + 12.0), 3.0, LAYER_DIM, align=TextEntityAlignment.MIDDLE_CENTER)

    end_x = base_x + overall * scale
    _text(msp, f"OVERALL = {overall:g} mm", (base_x, y - 12.0), 4.0, LAYER_DIM)


def _draw_end_section(msp, info, origin, scale=1.0):
    renderer = EndSectionRenderer(info)
    return renderer.render(msp, origin, scale)


def _draw_title_block(msp, member, rules, x, y):
    data = {
        "drawing_id": f"429B{member.get('backmark')}",
        "backmark": member.get("backmark"),
        "section": member.get("section"),
        "length_mm": member.get("length_mm"),
        "quantity": member.get("qty") if member.get("qty") is not None else member.get("quantity"),
        "standard": rules.get("standard", "IS 802"),
    }
    renderer = TitleBlockRenderer(data)
    renderer.render(msp, (x, y))


# ============================================================
# CANONICAL JSON GENERATOR
# ============================================================

def build_canonical_shop_json(
    member: Any,
    holes: list[dict[str, Any] | Hole],
    rules: dict[str, Any],
    gauge_mm: float | tuple[float, ...] | list[float] | None = None,
) -> dict[str, Any]:
    """Build the canonical ShopDrawingModel JSON representation."""
    if hasattr(member, "to_dict"):
        mem_dict = member.to_dict()
    else:
        mem_dict = dict(member)

    bolt_counts = Counter()
    hole_diameters = Counter()
    norm_holes: list[Hole] = []

    # Determine the primary gauge for transverse_mm population
    primary_gauge = None
    if isinstance(gauge_mm, (list, tuple)):
        primary_gauge = float(gauge_mm[0]) if gauge_mm else None
    elif gauge_mm is not None:
        primary_gauge = float(gauge_mm)

    # Default provenance for engineering-input-sourced holes
    default_provenance = {
        "source_kind": "ENGINEERING_INPUT",
        "source_id": "connection_design.json",
        "entity_id": "",
        "confidence": 1.0,
        "rule_id": "approved_design_input",
        "notes": "Hole from approved engineering connection design",
    }

    for hole in holes:
        if isinstance(hole, Hole):
            # Inject provenance if missing
            if hole.provenance is None:
                hole.provenance = ProvenanceRecord(
                    source_kind=SourceKind.DESIGN_INPUT,
                    source_id="connection_design.json",
                    entity_id=hole.hole_id or "",
                    confidence=1.0,
                    rule_id="approved_design_input",
                    notes="Hole from approved engineering connection design",
                )
            # Inject transverse_mm if missing
            if hole.transverse_mm is None:
                if hole.edge_distance_mm is not None:
                    hole.transverse_mm = hole.edge_distance_mm
                elif primary_gauge is not None:
                    hole.transverse_mm = primary_gauge
            norm_holes.append(hole)
            bolt = hole.nominal_bolt_diameter_mm
            dia = hole.diameter_mm
        elif isinstance(hole, dict):
            # Inject provenance if missing
            if hole.get("provenance") is None:
                hole["provenance"] = dict(default_provenance)
            # Inject transverse_mm if missing
            if hole.get("transverse_mm") is None:
                edge = hole.get("edge_distance_mm")
                if edge is not None:
                    hole["transverse_mm"] = float(edge)
                elif primary_gauge is not None:
                    hole["transverse_mm"] = primary_gauge
            norm_holes.append(Hole.from_dict(hole))
            bolt = hole.get("nominal_bolt_diameter_mm")
            dia = hole.get("hole_diameter_mm", hole.get("diameter_mm"))
        else:
            continue

        if bolt is not None:
            bolt_counts[f"M{int(float(bolt))}"] += 1
        if dia is not None:
            hole_diameters[f"{float(dia):g}"] += 1

    ends = {}
    if "ends" in mem_dict and isinstance(mem_dict["ends"], dict):
        for end_name, end in sorted(mem_dict["ends"].items()):
            ends[str(end_name)] = {
                "holes": _expand_end_holes(end_name, end),
                "pattern": end.get("pattern") if isinstance(end, dict) else None,
            }

    raw_qty = mem_dict.get("qty") if mem_dict.get("qty") is not None else mem_dict.get("quantity")
    qty_val = int(raw_qty) if raw_qty is not None else None

    # Determine quantity_source
    qty_source = mem_dict.get("quantity_source")
    if qty_source is None and qty_val is not None:
        qty_source = "ENGINEERING_INPUT"

    # Compute dimension chains (Level 1, Level 2, and optional Level 3 gauge)
    chains = (
        build_dimension_chains(float(mem_dict["length_mm"]), holes, gauge_mm=gauge_mm)
        if holes else []
    )

    section_info = _parse_section(mem_dict.get("section"))
    canonical_member = Member.from_dict(mem_dict)

    # Inject provenance and quantity_source on canonical member dict
    member_dict = canonical_member.to_dict()
    if member_dict.get("provenance") is None:
        member_dict["provenance"] = {
            "source_kind": "ENGINEERING_INPUT",
            "source_id": "connection_design.json",
            "entity_id": str(mem_dict.get("backmark", "")),
            "confidence": 1.0,
            "rule_id": "approved_design_input",
            "notes": "Member from approved engineering design input",
        }
    if member_dict.get("quantity_source") is None:
        member_dict["quantity_source"] = qty_source

    title_block = {
        "job_no": str(mem_dict.get("job_no", "") or mem_dict.get("job", "") or "WO_429"),
        "drawing_id": f"429B{mem_dict['backmark']}",
        "backmark": str(mem_dict["backmark"]),
        "section": str(mem_dict["section"]),
        "length_mm": float(mem_dict["length_mm"]),
        "quantity": qty_val,
        "standard": rules.get("standard", "IS 802"),
        "date": date.today().isoformat(),
        "rev": "0",
        "status": str(mem_dict.get("status", "FABRICATION READY")),
    }

    return {
        "schema_version": "shop-drawing-v1",
        "drawing_id": f"429B{mem_dict['backmark']}",
        "backmark": str(mem_dict["backmark"]),
        "member": member_dict,
        "section": str(mem_dict["section"]),
        "length_mm": float(mem_dict["length_mm"]),
        "qty": qty_val,
        "quantity": qty_val,
        "ends": ends,
        "holes": [h.to_dict() for h in norm_holes],
        "hole_positions": sorted(
            [h.to_dict() for h in norm_holes],
            key=lambda h: (str(h.get("end")), float(h.get("along_mm", 0))),
        ),
        "per_piece": dict(sorted(bolt_counts.items())),
        "hole_diameters_mm": dict(sorted(hole_diameters.items())),
        "dimension_chains": [
            [item.to_dict() for item in chain]
            for chain in chains
        ],
        "end_section": section_info,
        "title_block": title_block,
        "status": str(mem_dict.get("status", "FABRICATION READY")),
        "generator": {
            "format": "DXF",
            "standard": rules.get("standard", "IS 802"),
            "source": "approved_design_input",
        },
    }


# ============================================================
# PDF EXPORT ENGINE
# ============================================================

def export_to_pdf(
    dxf_path: str | Path,
    pdf_path: str | Path,
    page_size: str = "A3",
    dpi: int = 300,
) -> Path:
    """Deterministic headless vector PDF export using ezdxf matplotlib addon."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    dxf_path = Path(dxf_path)
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    doc = ezdxf.readfile(dxf_path)
    figsize = (11.69, 8.27) if str(page_size).upper() == "A4" else (16.54, 11.69)

    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal", "datalim")

    ctx = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    frontend = Frontend(ctx, backend)
    frontend.draw_layout(doc.modelspace(), finalize=True)

    fig.savefig(str(pdf_path), format="pdf", dpi=dpi)
    plt.close(fig)

    return pdf_path


# ============================================================
# SINGLE DRAWING GENERATION
# ============================================================

def generate_member_dxf(
    member: Any,
    rules: dict[str, Any],
    out_path: str | Path,
    export_pdf: bool = True,
) -> dict[str, Any]:
    """Generate complete CAD shop drawing package: DXF deliverable, JSON sidecar, and PDF export."""
    holes = validate_member_design(member, rules)

    doc = ezdxf.new("R2018")
    _layers(doc)
    msp = doc.modelspace()

    length = float(member["length_mm"])
    section_str = str(member["section"])
    info = _parse_section(section_str)

    # Look up gauge distance for angles (supports explicit member override or IS 802 table)
    gauge_mm = member.get("gauge_mm")
    if gauge_mm is None and info.get("kind") == "ANGLE":
        try:
            gauges = get_gauge_distances(section_str, rules)
            gauge_mm = gauges if len(gauges) > 1 else gauges[0]
        except Exception:
            gauge_mm = 28.0
    elif info.get("kind") == "FLAT":
        # For flat members, derive gauge from first hole's edge_distance_mm
        for h in holes:
            edge = h.get("edge_distance_mm") if isinstance(h, dict) else getattr(h, "edge_distance_mm", None)
            if edge is not None and float(edge) > 0:
                gauge_mm = float(edge)
                break

    # Auto-scale engine taking member profile height into account
    member_height = max(
        float(info.get("leg_b_mm") or info.get("leg_a_mm") or info.get("width_mm") or 50.0),
        50.0,
    )
    scale, scale_label = select_drawing_scale(length, max_width_mm=330.0, height_mm=member_height, max_height_mm=70.0)

    # Sheet layout (A3 Landscape 420 x 297 mm)
    _draw_sheet_frame(msp, width=420.0, height=297.0, margin=10.0)

    # Elevation Origin & Rendering
    origin = (55.0, 205.0)
    if info.get("kind") == "ANGLE":
        leg_a = float(info.get("leg_a_mm", 50))
        leg_b = float(info.get("leg_b_mm", 50))
        thk = float(info.get("thickness_mm", 5))
        miter_cut = bool(member.get("miter_cut", True))
        renderer = AngleMemberRenderer(leg_a, leg_b, thk, length, gauge_mm=gauge_mm, miter_cut=miter_cut)
        renderer.render(msp, origin, holes, scale)
        bottom_y = origin[1]
    elif info.get("kind") == "FLAT":
        w = float(info.get("width_mm", 45))
        thk = float(info.get("thickness_mm", 4))
        renderer = FlatMemberRenderer(w, thk, length)
        renderer.render(msp, origin, holes, scale)
        bottom_y = origin[1] - (w * scale) / 2.0
    else:
        # Fallback unknown section
        _line(msp, (origin[0], origin[1]), (origin[0] + length * scale, origin[1]), LAYER_MEMBER)
        bottom_y = origin[1]

    # Multi-tier dimension rendering (Level 1 holes, Level 2 overall, Level 3 gauge)
    dim_renderer = MultiTierDimensionRenderer(length, holes, gauge_mm=gauge_mm)
    dim_renderer.render(
        msp,
        origin,
        scale=scale,
        dim_y_level1=bottom_y - 20.0,
        dim_y_level2=bottom_y - 35.0,
        member_bottom_y=bottom_y,
    )

    # Cross-section End Section rendering (Lower Left)
    end_renderer = EndSectionRenderer(info)
    end_renderer.render(
        msp,
        (55.0, 50.0),
        scale=0.5 if max(float(info.get("leg_a_mm") or 0), float(info.get("width_mm") or 0)) > 65 else 1.0,
    )

    # Bolt / Hole Schedule Table (Lower Right / Middle Right)
    raw_qty = member.get("qty") if member.get("qty") is not None else member.get("quantity")
    qty_val = int(raw_qty) if raw_qty is not None else 1
    sched_renderer = HoleScheduleRenderer(holes, member_qty=qty_val)
    sched_renderer.render(msp, origin=(245.0, 75.0), width=160.0)

    # Title Block (Lower Right)
    tb_data = {
        "drawing_id": f"429B{member['backmark']}",
        "backmark": str(member["backmark"]),
        "section": section_str,
        "length_mm": length,
        "quantity": qty_val,
        "standard": rules.get("standard", "IS 802"),
        "status": str(member.get("status", "FABRICATION READY")),
        "date": date.today().isoformat(),
        "rev": "0",
    }
    tb_renderer = TitleBlockRenderer(tb_data)
    tb_renderer.render(msp, origin=(245.0, 15.0), width=160.0, height=55.0)

    # Scale note
    _text(msp, f"SCALE: {scale_label}", (55.0, 15.0), height=2.8, layer=LAYER_TEXT)

    # Save DXF deliverable
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out_path)

    # Canonical JSON sidecar
    generated_json = build_canonical_shop_json(member, holes, rules, gauge_mm=gauge_mm)
    generated_json["scale"] = {"factor": scale, "label": scale_label}
    json_path = out_path.with_suffix(".json")
    json_path.write_text(json.dumps(generated_json, indent=2), encoding="utf-8")

    # PDF export
    pdf_path = out_path.with_suffix(".pdf")
    if export_pdf:
        try:
            export_to_pdf(out_path, pdf_path)
        except Exception as ex:
            import logging
            logging.getLogger("shop_drawing").warning("PDF export failed for %s: %s", out_path, ex)

    return {
        "backmark": str(member["backmark"]),
        "drawing_id": f"429B{member['backmark']}",
        "path": str(out_path),
        "dxf": str(out_path),
        "json_path": str(json_path),
        "json": str(json_path),
        "pdf_path": str(pdf_path),
        "pdf": str(pdf_path),
        "hole_count": len(holes),
        "holes": len(holes),
        "qty": qty_val,
        "quantity": qty_val,
        "scale": scale_label,
    }


# ============================================================
# FULL JOB GENERATOR
# ============================================================

def generate_job(
    design_path: str | Path,
    rules_path: str | Path,
    out_dir: str | Path,
    export_pdf: bool = True,
    buildable_marks: set | list | None = None,
    only_buildable: bool = False,
) -> list[dict[str, Any]]:
    """Generate complete fabrication shop packages for all members in an approved design input."""
    design_path = Path(design_path)
    rules = load_rules(rules_path)
    design = json.loads(design_path.read_text(encoding="utf-8"))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    target_marks = None
    if buildable_marks is not None:
        target_marks = {str(m).strip() for m in buildable_marks}

    results = []
    for member in design.get("members", []):
        backmark = str(member.get("backmark", "")).strip()
        if target_marks is not None and backmark not in target_marks:
            continue
        if only_buildable:
            q = member.get("qty") if member.get("qty") is not None else member.get("quantity")
            if q is None:
                continue
            try:
                if int(q) <= 0:
                    continue
            except (ValueError, TypeError):
                continue
            holes = validate_member_design(member, rules)
            if not holes:
                continue

        dxf_path = out / f"429B{backmark}.dxf"
        result = generate_member_dxf(member, rules, dxf_path, export_pdf=export_pdf)
        results.append(result)

    manifest = {
        "schema_version": "generation-v1",
        "standard": rules["standard"],
        "design_input": str(design_path),
        "drawing_count": len(results),
        "drawings": results,
    }

    (out / "generation_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    return results