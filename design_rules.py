"""Engineering-detail rule engine.

No project standard is assumed. A JSON rules file supplies the approved
standard values. The engine validates a supplied hole pattern and can expand
an explicitly approved pitch/edge rule into positions. It never chooses a
standard silently.
"""
from __future__ import annotations
import json, math, re
from pathlib import Path

class DesignRuleError(ValueError): pass

# Standard IS 802 angle gauge distance table (mm from heel based on leg width w)
IS_802_ANGLE_GAUGES: dict[float, float] = {
    40.0: 22.0,
    45.0: 25.0,
    50.0: 28.0,
    55.0: 30.0,
    60.0: 35.0,
    65.0: 35.0,
    75.0: 40.0,
    90.0: 50.0,
    100.0: 55.0,
    110.0: 60.0,
    130.0: 70.0,
    150.0: 55.0,
}

# IS 802 allows double gauge lines for 150mm leg (55mm heel to line 1, 95mm heel to line 2)
IS_802_ANGLE_GAUGES_DOUBLE: dict[float, tuple[float, float]] = {
    150.0: (55.0, 95.0),
}

def load_rules(path):
    data=json.loads(Path(path).read_text())
    if not data.get("standard") or data["standard"] in ("UNSPECIFIED",None):
        raise DesignRuleError("Engineering standard is not specified in rules JSON")
    return data

def _rule(rules,hole_dia):
    for r in rules.get("hole_rules",[]):
        if abs(float(r["hole_diameter_mm"])-float(hole_dia))<1e-6: return r
    raise DesignRuleError(f"No hole rule for diameter {hole_dia} mm")

def validate_pattern(rules, member_length, holes):
    errors=[]; warnings=[]
    by_dia={}
    for h in holes:
        x=float(h["along_mm"]); d=float(h["hole_diameter_mm"])
        t=h.get("transverse_mm")
        t_key = round(float(t), 2) if t is not None else None
        if x<0 or x>member_length: errors.append(f"hole at {x:.2f} outside member length {member_length:.2f}")
        try: rr=_rule(rules,d)
        except DesignRuleError as e: errors.append(str(e)); continue
        by_dia.setdefault((d, t_key),[]).append(x)
        edge=float(h.get("edge_distance_mm",rr.get("min_edge_distance_mm",0)))
        if edge < float(rr.get("min_edge_distance_mm",0)): errors.append(f"edge distance {edge:g} < minimum for Ø{d:g}")
    for (d, _),vals in by_dia.items():
        vals.sort(); rr=_rule(rules,d); min_pitch=float(rr.get("min_pitch_mm",0))
        for a,b in zip(vals,vals[1:]):
            if b-a<min_pitch: errors.append(f"pitch {b-a:.2f} < minimum {min_pitch:.2f} for Ø{d:g}")
    return {"valid":not errors,"errors":errors,"warnings":warnings}

def expand_pitch_pattern(rules, member_length, count, hole_diameter_mm, start_from_end_mm, pitch_mm, edge_distance_mm=None, nominal_bolt_diameter_mm=None):
    rr=_rule(rules,hole_diameter_mm)
    if count<1: raise DesignRuleError("count must be >= 1")
    if pitch_mm<float(rr.get("min_pitch_mm",0)): raise DesignRuleError("requested pitch violates minimum pitch rule")
    edge=float(edge_distance_mm if edge_distance_mm is not None else rr.get("min_edge_distance_mm",0))
    if edge<float(rr.get("min_edge_distance_mm",0)): raise DesignRuleError("requested edge distance violates minimum")
    vals=[float(start_from_end_mm)+i*float(pitch_mm) for i in range(count)]
    if vals[-1]>member_length-edge: raise DesignRuleError("expanded pattern exceeds member length after edge allowance")
    return [{"along_mm":v,"hole_diameter_mm":float(hole_diameter_mm),"nominal_bolt_diameter_mm":float(nominal_bolt_diameter_mm) if nominal_bolt_diameter_mm is not None else None,"edge_distance_mm":edge} for v in vals]

def get_gauge_distance(section_str: str | float | int, rules: dict | None = None, gauge_line: int = 1) -> float:
    """Look up standard IS 802 angle gauge distance (mm from heel) for a section.
    
    If rules is provided and contains 'angle_gauge_rules' or 'angle_gauges',
    those rules take precedence over the built-in IS 802 table.
    For leg width 150 mm, IS 802 specifies double gauge lines (55 mm and 95 mm).
    gauge_line=1 returns primary line (55 mm), gauge_line=2 returns second line (95 mm).
    """
    if section_str is None:
        raise DesignRuleError("Section string cannot be None")

    leg_w = None
    if isinstance(section_str, (int, float)):
        leg_w = float(section_str)
    else:
        s = str(section_str).strip().upper().replace(" ", "")

        # Flat sections: e.g. 4THKX45, HT8THKX154
        m_thk = re.search(r"(?:HT)?(\d+(?:\.\d+)?)THKX(\d+(?:\.\d+)?)", s)
        if m_thk:
            width = float(m_thk.group(2))
            return width / 2.0

        # Flat sections: e.g. FLAT4X45, 4X45 (if FLAT in s)
        m_flat = re.search(r"^(?:FLAT)?(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)", s)
        if ("FLAT" in s or s.startswith("PL")) and m_flat:
            width = float(m_flat.group(2))
            return width / 2.0

        # Angle sections: e.g. L50x50x5, HTL45x45x5, HT 50x50x5, ISA 65x65x6, 50x50x5
        m_angle = re.search(r"^(?:ISA|HTL|HT|L)?(\d+(?:\.\d+)?)", s)
        if m_angle:
            try:
                leg_w = float(m_angle.group(1))
            except ValueError:
                pass

    if leg_w is None:
        raise DesignRuleError(f"Cannot parse leg width from section '{section_str}'")

    # Check custom rules first if provided
    if rules:
        gauge_rules = rules.get("angle_gauge_rules") or rules.get("angle_gauges")
        if isinstance(gauge_rules, dict):
            # Check line 2 specific key e.g. "150_line2"
            if gauge_line == 2:
                for k2 in [f"{leg_w:g}_line2", f"{int(leg_w)}_line2", f"{leg_w:g}_2"]:
                    if k2 in gauge_rules:
                        try:
                            return float(gauge_rules[k2])
                        except (ValueError, TypeError):
                            pass

            for k, v in gauge_rules.items():
                try:
                    if abs(float(k) - leg_w) < 1e-4:
                        if isinstance(v, (list, tuple)):
                            idx = gauge_line - 1
                            if 0 <= idx < len(v):
                                return float(v[idx])
                            raise DesignRuleError(f"Gauge line {gauge_line} not defined for leg {leg_w:g} mm")
                        elif isinstance(v, str) and "/" in v:
                            parts = [float(p.strip()) for p in v.split("/")]
                            idx = gauge_line - 1
                            if 0 <= idx < len(parts):
                                return parts[idx]
                            raise DesignRuleError(f"Gauge line {gauge_line} not defined for leg {leg_w:g} mm")
                        if gauge_line == 1:
                            return float(v)
                except (ValueError, TypeError):
                    continue
        elif isinstance(gauge_rules, list):
            for item in gauge_rules:
                if isinstance(item, dict):
                    w_val = item.get("leg_width_mm") or item.get("width_mm") or item.get("leg")
                    if w_val is not None and abs(float(w_val) - leg_w) < 1e-4:
                        g_val = item.get("gauge_mm", item.get("gauge", 0.0))
                        if isinstance(g_val, (list, tuple)):
                            idx = gauge_line - 1
                            if 0 <= idx < len(g_val):
                                return float(g_val[idx])
                        if gauge_line == 1:
                            return float(g_val)

    # Fallback to standard IS 802 table
    if gauge_line == 2:
        for w_std, g_pair in IS_802_ANGLE_GAUGES_DOUBLE.items():
            if abs(w_std - leg_w) < 1e-4:
                return float(g_pair[1])
        raise DesignRuleError(f"No second gauge line rule for angle leg width {leg_w:g} mm")

    for w_std, g_std in IS_802_ANGLE_GAUGES.items():
        if abs(w_std - leg_w) < 1e-4:
            return float(g_std)

    raise DesignRuleError(f"No gauge distance rule for angle leg width {leg_w:g} mm")


def get_gauge_distances(section_str: str | float | int, rules: dict | None = None) -> tuple[float, ...]:
    """Look up all standard IS 802 gauge distances (mm from heel) for a section."""
    g1 = get_gauge_distance(section_str, rules, gauge_line=1)
    try:
        g2 = get_gauge_distance(section_str, rules, gauge_line=2)
        return (g1, g2)
    except DesignRuleError:
        return (g1,)
