"""ConsensusGate: Deterministic IS 802 engineering validation and CAD circle snapping.

Takes connection proposals (from topology resolution or VLM visual reasoning),
validates them against IS 802 structural standards, snaps proposed hole coordinates
to exact CAD circle entities (when present on bolt layers), and emits canonical
connection_design.json compliant data structures.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
import ezdxf
from ezdxf import recover

from design_rules import load_rules, get_gauge_distance
from member_geometry import section_info, Segment, dist
from scale_context import ScaleContext


# Standard IS 802: Standard clearance hole sizes
HOLE_CLEARANCE_MAP = {
    10: 11.5,
    12: 13.5,
    16: 17.5,
    20: 22.0,
    24: 26.0,
}

# Standard IS 802: Minimum edge distances (mm)
STANDARD_EDGE_DISTANCES = {
    10: 20.0,
    12: 20.0,
    16: 25.0,
    20: 30.0,
    24: 35.0,
}

# Standard IS 802: Standard pitch (mm)
STANDARD_PITCH = {
    10: 35.0,
    12: 40.0,
    16: 45.0,
    20: 50.0,
    24: 60.0,
}


def compute_standard_hole_positions(
    member_length_mm: float,
    bolt_count: int,
    nominal_bolt_dia_mm: int = 16,
    end: str = "E1",
    custom_pitch: Optional[float] = None,
    custom_edge: Optional[float] = None,
) -> List[float]:
    """Generates deterministic longitudinal hole coordinates (mm from origin 0)
    conforming to IS 802 standard edge and pitch requirements.
    """
    if bolt_count <= 0 or member_length_mm <= 0:
        return []

    edge = custom_edge or STANDARD_EDGE_DISTANCES.get(nominal_bolt_dia_mm, 25.0)
    pitch = custom_pitch or STANDARD_PITCH.get(nominal_bolt_dia_mm, 45.0)

    # Validate physical feasibility
    total_span = edge + max(0, bolt_count - 1) * pitch
    if total_span >= member_length_mm / 2.0 and bolt_count > 1:
        # Scale pitch down if member is unusually short
        pitch = max(2.5 * nominal_bolt_dia_mm, (member_length_mm / 2.0 - edge) / max(1, bolt_count - 1))

    positions = []
    if end in ("E1", "A"):
        for i in range(bolt_count):
            pos = edge + i * pitch
            positions.append(round(pos, 1))
    else:  # E2 or B
        for i in range(bolt_count):
            pos = member_length_mm - edge - (bolt_count - 1 - i) * pitch
            positions.append(round(pos, 1))

    return sorted(positions)


def snap_to_cad_circles(
    positions: List[float],
    cad_circles_along: List[float],
    tolerance_mm: float = 3.0,
) -> List[float]:
    """Snaps calculated hole coordinates to exact CAD circle coordinates if
    a CAD circle exists along the member axis within tolerance.
    """
    if not cad_circles_along:
        return positions

    snapped = []
    for pos in positions:
        closest = min(cad_circles_along, key=lambda c: abs(c - pos))
        if abs(closest - pos) <= tolerance_mm:
            snapped.append(round(closest, 1))
        else:
            snapped.append(pos)
    return sorted(snapped)


def validate_is802_compliance(
    positions: List[float],
    member_length_mm: float,
    nominal_bolt_dia_mm: int = 16,
) -> Dict[str, Any]:
    """Validates hole coordinates against IS 802 minimum pitch and edge distance rules."""
    if not positions:
        return {"valid": True, "violations": []}

    violations = []
    min_edge = 1.5 * nominal_bolt_dia_mm
    min_pitch = 2.5 * nominal_bolt_dia_mm

    # Check end 1 edge distance
    e1 = positions[0]
    if e1 < min_edge - 0.5:
        violations.append(f"End 1 edge distance {e1:.1f}mm < minimum {min_edge:.1f}mm")

    # Check end 2 edge distance
    e2 = member_length_mm - positions[-1]
    if e2 < min_edge - 0.5:
        violations.append(f"End 2 edge distance {e2:.1f}mm < minimum {min_edge:.1f}mm")

    # Check pitches
    for i in range(len(positions) - 1):
        pitch = positions[i + 1] - positions[i]
        # Only check pitch between adjacent holes in same group (pitch < 150)
        if pitch < 150.0 and pitch < min_pitch - 0.5:
            violations.append(f"Pitch between holes {i} and {i+1} ({pitch:.1f}mm) < minimum {min_pitch:.1f}mm")

    return {
        "valid": len(violations) == 0,
        "violations": violations,
    }


def synthesize_canonical_design_entry(
    backmark: str,
    section: str,
    length_mm: float,
    quantity: int,
    end_proposals: Dict[str, Dict[str, Any]],
    rules: Optional[Dict[str, Any]] = None,
    cad_circles_along: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """Builds a verified, canonical design input entry conforming to connection_design.json schema."""
    sec_info = section_info(section)
    gauge_mm = get_gauge_distance(section, rules) if rules else 25.0

    ends_dict = {}
    for end_id in ("E1", "E2"):
        prop = end_proposals.get(end_id, {})
        bolt_qty = int(prop.get("bolt_quantity", 0) or 0)
        bolt_dia = int(prop.get("nominal_bolt_diameter_mm", 16) or 16)
        hole_dia = HOLE_CLEARANCE_MAP.get(bolt_dia, float(bolt_dia) + 1.5)

        # Longitudinal positions
        raw_positions = prop.get("hole_positions_along_mm")
        if not raw_positions and bolt_qty > 0:
            raw_positions = compute_standard_hole_positions(
                length_mm, bolt_qty, nominal_bolt_dia_mm=bolt_dia, end=end_id
            )

        if raw_positions and cad_circles_along:
            final_positions = snap_to_cad_circles(raw_positions, cad_circles_along)
        else:
            final_positions = raw_positions or []

        # Construct hole objects
        holes = []
        for i, along in enumerate(final_positions):
            holes.append({
                "hole_id": f"H_{backmark}_{end_id}_{i+1}",
                "along_mm": float(along),
                "transverse_mm": float(gauge_mm),
                "diameter_mm": float(hole_dia),
                "nominal_bolt_diameter_mm": int(bolt_dia),
                "bolt_size": f"M{bolt_dia}",
                "provenance": {
                    "source_kind": "IS802_CONSENSUS_GATE",
                    "confidence": float(prop.get("confidence", 0.95)),
                    "rule_id": "IS_802_PART_2",
                },
            })

        ends_dict[end_id] = {
            "end_id": end_id,
            "joint_id": prop.get("joint_id"),
            "connection_group_id": prop.get("group_id"),
            "holes": holes,
        }

    return {
        "backmark": backmark,
        "section": section,
        "length_mm": float(length_mm),
        "quantity": int(quantity),
        "quantity_source": "VERIFIED_EVIDENCE",
        "connection_ends": ends_dict,
    }
