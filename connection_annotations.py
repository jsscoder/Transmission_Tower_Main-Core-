"""Connection/bolt annotation extraction from the tower assembly DXF.

This module reads the native B1 bolt-callout blocks (with ATTRIB text) rather
than OCR'ing the raster.  It intentionally keeps every raw annotation and
separates parsing from member/joint association.  No shop-drawing examples are
hard-coded here.
"""
from __future__ import annotations

import csv, json, math, re
from dataclasses import dataclass, asdict
from pathlib import Path
from collections import defaultdict
import ezdxf
from ezdxf import recover

BOLT_BLOCK_NAMES = {"B1"}
# Examples in this drawing: 1-16x50, 2-16x45, 1-16#12, 2-16#8, 1-12x35
CALLOUT_RE = re.compile(r"^(?P<qty>\d+)\s*-\s*(?P<dia>\d+)\s*(?P<kind>[x#])\s*(?P<value>\d+)(?P<star>\*)?$")

@dataclass
class BoltCallout:
    id: int
    block: str
    x: float
    y: float
    raw: str
    quantity: int | None
    nominal_diameter_mm: int | None
    qualifier: str | None       # x = length, # = drawing code/variant
    value: int | None
    starred: bool
    layer: str

@dataclass
class CalloutGroup:
    id: int
    x: float
    y_min: float
    y_max: float
    callouts: list[dict]


def _parse_callout(raw: str):
    m = CALLOUT_RE.fullmatch(raw.strip())
    if not m:
        return None
    return {
        "quantity": int(m.group("qty")),
        "nominal_diameter_mm": int(m.group("dia")),
        "qualifier": m.group("kind"),
        "value": int(m.group("value")),
        "starred": bool(m.group("star")),
    }


def extract_bolt_callouts(dxf_path: str):
    doc, auditor = recover.readfile(dxf_path)
    if auditor.has_errors:
        raise RuntimeError(f"DXF has structural errors: {auditor.errors}")
    msp = doc.modelspace()
    rows = []
    idx = 0
    for ins in msp.query("INSERT"):
        if ins.dxf.name not in BOLT_BLOCK_NAMES:
            continue
        attrs = list(ins.attribs)
        if not attrs:
            continue
        # B1 has one meaningful ATTREF; keep all attributes if a future block
        # carries more than one so no information is silently discarded.
        for a in attrs:
            raw = a.dxf.text.strip()
            parsed = _parse_callout(raw)
            if parsed is None:
                continue
            rows.append(BoltCallout(
                id=idx,
                block=ins.dxf.name,
                x=float(ins.dxf.insert.x), y=float(ins.dxf.insert.y),
                raw=raw, layer=ins.dxf.layer, **parsed
            ))
            idx += 1
    return rows


def group_stacked_callouts(rows, x_tol=8.0, y_gap=95.0):
    """Group vertically stacked B1 rectangles belonging to one annotation box.

    The supplied drawing uses a 250x~71 block and stacks related rows at about
    62.5 drawing units.  This is geometry-driven and does not depend on any
    particular backmark.
    """
    groups = []
    unused = sorted(rows, key=lambda r: (r.x, r.y))
    for r in unused:
        candidates = [g for g in groups if abs(g.x-r.x) <= x_tol and r.y >= g.y_min-y_gap and r.y <= g.y_max+y_gap]
        if candidates:
            g = min(candidates, key=lambda z: abs(z.x-r.x)+abs(max(z.y_min-r.y, r.y-z.y_max, 0)))
            g.callouts.append(asdict(r)); g.y_min=min(g.y_min,r.y); g.y_max=max(g.y_max,r.y)
        else:
            groups.append(CalloutGroup(len(groups),r.x,r.y,r.y,[asdict(r)]))
    # normalize ids and sort by drawing position
    groups.sort(key=lambda g:(g.y_min,g.x))
    for i,g in enumerate(groups): g.id=i; g.callouts.sort(key=lambda z:z["y"])
    return groups


def aggregate_group(group):
    by_dia=defaultdict(int)
    by_dia_len=defaultdict(int)
    for c in group.callouts:
        if c["quantity"] is None: continue
        dia=c["nominal_diameter_mm"]
        by_dia[dia] += c["quantity"]
        if c["qualifier"] == "x":
            by_dia_len[(dia,c["value"])] += c["quantity"]
    return {
        "bolt_quantity_by_diameter": dict(sorted(by_dia.items())),
        "bolt_quantity_by_diameter_length": {f"M{d}x{l}":q for (d,l),q in sorted(by_dia_len.items())}
    }


def groups_as_dicts(groups):
    """Convert list[CalloutGroup] (as returned by group_stacked_callouts)
    into the dict shape that downstream stages (connection_topology,
    connection_resolver) expect: 'group_id' key (not 'id'), plus bolt
    aggregates already computed. connection_association.associate() builds
    this same shape inline; this is the shared version so every caller
    stays consistent instead of re-deriving it slightly differently."""
    out = []
    for g in groups:
        d = asdict(g)
        d["group_id"] = d.pop("id")
        d.update(aggregate_group(g))
        out.append(d)
    return out


def write_callout_outputs(rows, groups, out_dir):
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    (out/"bolt_callouts.json").write_text(json.dumps([asdict(r) for r in rows],indent=2))
    payload=[]
    for g in groups:
        x=asdict(g);x.update(aggregate_group(g));payload.append(x)
    (out/"connection_callout_groups.json").write_text(json.dumps(payload,indent=2))
    with open(out/"bolt_callouts.csv","w",newline="") as f:
        fields=["id","block","x","y","raw","quantity","nominal_diameter_mm","qualifier","value","starred","layer"]
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows([asdict(r) for r in rows])
    with open(out/"connection_callout_groups.csv","w",newline="") as f:
        fields=["group_id","x","y_min","y_max","raw_callouts","bolt_summary"]
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for g in payload:
            w.writerow({"group_id":g["id"],"x":g["x"],"y_min":g["y_min"],"y_max":g["y_max"],
                        "raw_callouts":" | ".join(c["raw"] for c in g["callouts"]),
                        "bolt_summary":json.dumps(g["bolt_quantity_by_diameter"])})
    return payload


def nearest_group_to_point(groups, point, block_width=250.66, block_height=71.39):
    """Distance from a point to the annotation rectangle, not its insertion point."""
    px,py=point
    best=None
    for g in groups:
        xmin,xmax=g.x,g.x+block_width
        ymin,ymax=g.y_min,g.y_max+block_height
        dx=max(xmin-px,0,px-xmax); dy=max(ymin-py,0,py-ymax)
        d=math.hypot(dx,dy)
        if best is None or d<best[0]: best=(d,g)
    return best
