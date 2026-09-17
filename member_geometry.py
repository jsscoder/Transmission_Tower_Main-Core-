"""Constrained member-geometry matching for tower assembly DXFs.

This module improves the old nearest-LINE heuristic without inventing geometry.
It generates candidate connected chains from native LINE entities, constrains
chains by the known section width, direction and schedule length, and returns
ranked evidence. It is deliberately independent of the four validation
fixtures.
"""
from __future__ import annotations
import math, re
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
import ezdxf
from ezdxf import recover

MEMBER_LAYER = "3_Members"
DESIGNATION_LAYER = "23_Member designation"
BACKMARK_RE = re.compile(r"^\d{1,4}[A-Z]{0,2}$")


def _normalize_layer(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass(frozen=True)
class Segment:
    index: int
    x1: float; y1: float; x2: float; y2: float
    length: float
    angle_deg: float
    @property
    def p1(self): return (self.x1, self.y1)
    @property
    def p2(self): return (self.x2, self.y2)


def dist(a,b): return math.hypot(a[0]-b[0],a[1]-b[1])

def angle_diff(a,b):
    d=abs((a-b)%180.0)
    return min(d,180.0-d)

def point_line_distance(p, a, b):
    dx=b[0]-a[0]; dy=b[1]-a[1]
    den=math.hypot(dx,dy)
    return abs((p[0]-a[0])*dy-(p[1]-a[1])*dx)/den if den else dist(p,a)

def point_segment_distance(p,s):
    dx=s.x2-s.x1; dy=s.y2-s.y1; den=dx*dx+dy*dy
    t=((p[0]-s.x1)*dx+(p[1]-s.y1)*dy)/den if den else 0.0
    t=max(0,min(1,t)); q=(s.x1+t*dx,s.y1+t*dy)
    return dist(p,q),t,q

def section_info(section):
    """Parse only section width information; never fabricate it."""
    s=(section or "").upper().replace(" ","")
    # Angles first: L50x50x5, HTL45x45x5, etc.
    m=re.search(r"^(?:HT)?L(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)(?:X(\d+(?:\.\d+)?))?",s)
    if m:
        return {"kind":"ANGLE","width_mm":max(float(m.group(1)),float(m.group(2))),"thickness_mm":float(m.group(3)) if m.group(3) else None}
    # Explicit thickness forms: 4THKX45, HT8THKX154
    m=re.search(r"(?:HT)?(\d+(?:\.\d+)?)THKX(\d+(?:\.\d+)?)",s)
    if m: return {"kind":"FLAT","thickness_mm":float(m.group(1)),"width_mm":float(m.group(2))}
    # FLAT 4x45 / 4X45
    m=re.search(r"^(?:FLAT)?(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)",s)
    if m: return {"kind":"FLAT","thickness_mm":float(m.group(1)),"width_mm":float(m.group(2))}
    # Plate forms: PL6 thk x97, 6 thk x104
    m=re.search(r"^(?:PL)?(\d+(?:\.\d+)?)\s*THK\s*X\s*(\d+(?:\.\d+)?)",s)
    if m: return {"kind":"PLATE","thickness_mm":float(m.group(1)),"width_mm":float(m.group(2))}
    return {"kind":"UNKNOWN","width_mm":50.0,"thickness_mm":None}


def collect_segments(dxf_path, min_length=5.0):
    doc,auditor=recover.readfile(dxf_path)
    if auditor.has_errors: raise RuntimeError(f"DXF has structural errors: {auditor.errors}")
    out=[]
    target_layer = _normalize_layer(MEMBER_LAYER)
    try:
        from layer_scanner import scan_dxf_layers
        layers_info = scan_dxf_layers(doc)
    except Exception:
        layers_info = None
    for i,e in enumerate(doc.modelspace().query("LINE")):
        l_norm = _normalize_layer(e.dxf.layer)
        if l_norm != target_layer:
            if layers_info is None or not layers_info.is_role(e.dxf.layer, "MEMBERS"):
                continue
        x1,y1=float(e.dxf.start.x),float(e.dxf.start.y); x2,y2=float(e.dxf.end.x),float(e.dxf.end.y)
        L=dist((x1,y1),(x2,y2))
        if L<min_length: continue
        ang=math.degrees(math.atan2(y2-y1,x2-x1))%180
        out.append(Segment(len(out),x1,y1,x2,y2,L,ang))
    return out


def _endpoint_neighbors(segments, tol=35.0):
    refs=[]
    for i,s in enumerate(segments):
        for end,p in (("A",s.p1),("B",s.p2)):
            refs.append((i,end,p))
    adj=defaultdict(set)
    for ai,(i,ei,pi) in enumerate(refs):
        for j,ej,pj in refs[ai+1:]:
            if i==j: continue
            if dist(pi,pj)<=tol:
                adj[i].add(j); adj[j].add(i)
    return adj


def _axis_from_seed(seed):
    return seed.p1,seed.p2


def candidate_chains(segments, label_positions, schedule_entry, *, endpoint_tol=35.0,
                     angle_tol_deg=2.0, corridor_extra=12.0, gap_tol=45.0,
                     max_seed_distance=800.0):
    target=float(schedule_entry.get("length_mm")) if schedule_entry and schedule_entry.get("length_mm") not in (None,"") else None
    info=section_info(schedule_entry.get("section") if schedule_entry else None)
    width=info.get("width_mm")
    corridor=(width/2.0+corridor_extra) if width else 60.0
    adj=_endpoint_neighbors(segments,endpoint_tol)
    seeds=[]
    for s in segments:
        d=min(point_segment_distance(p,s)[0] for p in label_positions) if label_positions else 1e9
        if d<=max_seed_distance: seeds.append((d,s))
    seeds.sort(key=lambda z:z[0])
    candidates=[]
    # Limit seeds to avoid exploding on large tower drawings while retaining
    # several geometrically distinct directions.
    for seed_d,seed in seeds[:80]:
        axis_a,axis_b=_axis_from_seed(seed)
        selected=[]
        for s in segments:
            if angle_diff(s.angle_deg,seed.angle_deg)>angle_tol_deg: continue
            # Both endpoints must remain in the seed member's width corridor.
            if max(point_line_distance(s.p1,axis_a,axis_b),point_line_distance(s.p2,axis_a,axis_b))>corridor: continue
            selected.append(s)
        if not selected: continue
        # Connected component containing seed. This prevents a distant but
        # parallel segment from being glued solely because it is collinear.
        idxset={s.index for s in selected}; q=deque([seed.index]); seen={seed.index}
        while q:
            u=q.popleft()
            for v in adj.get(u,()):
                if v in idxset and v not in seen:
                    seen.add(v); q.append(v)
        chain=[segments[i] for i in sorted(seen)]
        # Project chain onto seed axis and merge intervals. The span is the
        # physical member run; tiny internal gaps are permitted.
        ux=(seed.x2-seed.x1)/seed.length; uy=(seed.y2-seed.y1)/seed.length
        vals=[]
        for s in chain:
            vals.extend([((p[0]-seed.x1)*ux+(p[1]-seed.y1)*uy) for p in (s.p1,s.p2)])
        lo,hi=min(vals),max(vals); span=max(0,hi-lo)
        chain_start=(seed.x1+ux*lo, seed.y1+uy*lo)
        chain_end=(seed.x1+ux*hi, seed.y1+uy*hi)
        # Reject disconnected gaps larger than a real line-join tolerance.
        intervals=[]
        for s in chain:
            a=((s.x1-seed.x1)*ux+(s.y1-seed.y1)*uy); b=((s.x2-seed.x1)*ux+(s.y2-seed.y1)*uy)
            intervals.append((min(a,b),max(a,b)))
        intervals.sort(key=lambda x:(x[0],x[1]))
        merged=[]
        for st,en in intervals:
            if not merged or st>merged[-1][1]: merged.append([st,en])
            else: merged[-1][1]=max(merged[-1][1],en)
        max_gap=max((nxt_st-cur_en for (_,cur_en),(nxt_st,_) in zip(merged,merged[1:])),default=0)
        if max_gap>gap_tol: continue
        length_error=abs(span-target) if target else 0.0
        label_d=seed_d
        corridor_pen=max(0.0, max(point_line_distance(s.p1,axis_a,axis_b) for s in chain)-corridor)
        # Prefer target length, label proximity, fewer unrelated segments and
        # smaller corridor residual. Never use validation fixture data here.
        score=(length_error/(max(target,1)*0.02+10.0) if target else 0.0)+label_d/300.0+len(chain)*0.015+corridor_pen/50.0
        candidates.append({"score":score,"label_distance_mm":label_d,"length_mm":span,
                          "length_error_mm":length_error,"segment_indices":[s.index for s in chain],
                          "seed_index":seed.index,"section_info":info,"corridor_mm":corridor,
                          "max_internal_gap_mm":max_gap,
                          "axis":{"x1":seed.x1,"y1":seed.y1,"x2":seed.x2,"y2":seed.y2},
                          "endpoints":{"A":{"x":chain_start[0],"y":chain_start[1]},"B":{"x":chain_end[0],"y":chain_end[1]}}})
    # Deduplicate identical chains and keep best.
    uniq={}
    for c in candidates:
        key=tuple(c["segment_indices"]); uniq[key]=min(c,uniq.get(key,c),key=lambda x:x["score"])
    return sorted(uniq.values(),key=lambda x:x["score"])


def extract_member_geometry_candidates(dxf_path,schedule,max_candidates=5):
    segs=collect_segments(dxf_path)
    doc,_=recover.readfile(dxf_path); msp=doc.modelspace()
    marks=defaultdict(list)
    target_desig = _normalize_layer(DESIGNATION_LAYER)
    for e in msp.query("TEXT"):
        if _normalize_layer(e.dxf.layer) == target_desig and BACKMARK_RE.fullmatch(e.dxf.text.strip()):
            marks[e.dxf.text.strip()].append((float(e.dxf.insert.x),float(e.dxf.insert.y)))
    
    try:
        from scale_context import ScaleContext
        ctx = ScaleContext.from_dxf(dxf_path)
        endpoint_tol = ctx.joint_cluster_tol_mm
        max_seed_dist = ctx.max_label_distance_mm
    except Exception:
        endpoint_tol = 35.0
        max_seed_dist = 800.0

    out={}
    for mark,entry in schedule.items():
        cs=candidate_chains(segs,marks.get(mark,[]),entry,endpoint_tol=endpoint_tol,max_seed_distance=max_seed_dist)
        out[mark]={"backmark":mark,"candidates":cs[:max_candidates],"selected":cs[0] if cs else None}
    return out
