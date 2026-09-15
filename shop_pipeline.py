"""Stage 4: native CAD member geometry + bolt evidence.

Uses constrained multi-LINE chains from member_geometry.py. The module never
uses the validation shop drawings as production input and never fabricates
holes, cuts, gauges or bolt counts.
"""
from __future__ import annotations
import csv, json, math
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, asdict
import ezdxf
from ezdxf import recover
from member_geometry import collect_segments, extract_member_geometry_candidates, point_segment_distance

BOLT_LAYER="5_Bolts"
HOLE_TO_BOLT={11.5:10,13.5:12,17.5:16,22.0:20,26.0:24}

@dataclass
class BoltEvidence:
    x: float; y: float; along_mm: float; perp_mm: float
    circle_diameter_mm: float; mapped_nominal_diameter_mm: int|None
    duplicate_count:int=1

def _bolt_circles(msp):
    out=[]
    for e in msp.query("CIRCLE"):
        if e.dxf.layer!=BOLT_LAYER: continue
        r=float(e.dxf.radius)
        if r>0: out.append((float(e.dxf.center.x),float(e.dxf.center.y),2*r))
    return out

def _dedupe(rows,pos_tol=2.0):
    out=[]
    for b in sorted(rows,key=lambda z:(z.along_mm,z.perp_mm,z.circle_diameter_mm)):
        hit=next((g for g in out if abs(g.along_mm-b.along_mm)<=pos_tol and abs(g.perp_mm-b.perp_mm)<=pos_tol and abs(g.circle_diameter_mm-b.circle_diameter_mm)<=0.1),None)
        if hit: hit.duplicate_count+=1
        else: out.append(b)
    return out

def _chain_axis(candidate,segs):
    a=segs[candidate["seed_index"]]
    return a

def _chain_bolt_evidence(candidate,segs,circles,search_radius=80.0):
    axis=_chain_axis(candidate,segs); dx=axis.x2-axis.x1; dy=axis.y2-axis.y1; L=axis.length
    ux,uy=dx/L,dy/L
    # Use the candidate span rather than the seed segment length.
    vals=[]
    for i in candidate["segment_indices"]:
        s=segs[i]
        vals += [(p[0]-axis.x1)*ux+(p[1]-axis.y1)*uy for p in (s.p1,s.p2)]
    lo,hi=min(vals),max(vals)
    rows=[]
    for x,y,hd in circles:
        along=(x-axis.x1)*ux+(y-axis.y1)*uy
        perp=abs((x-axis.x1)*uy-(y-axis.y1)*ux)
        if lo-50<=along<=hi+50 and perp<=search_radius:
            nearest=min(HOLE_TO_BOLT,key=lambda k:abs(k-hd))
            if abs(nearest-hd)<=0.15:
                rows.append(BoltEvidence(x,y,along-lo,perp,hd,HOLE_TO_BOLT[nearest]))
    return _dedupe(rows)

def extract_member_evidence(dxf_path,schedule,max_label_distance=800,member_search_radius=80):
    doc,auditor=recover.readfile(dxf_path)
    if auditor.has_errors: raise RuntimeError(f"DXF has structural errors: {auditor.errors}")
    msp=doc.modelspace(); segs=collect_segments(dxf_path); circles=_bolt_circles(msp)
    candidates=extract_member_geometry_candidates(dxf_path,schedule)
    result={}
    for mark,entry in schedule.items():
        data=candidates.get(mark,{"candidates":[],"selected":None}); ranked=data.get("candidates",[])
        if not ranked:
            result[mark]={"backmark":mark,"section":entry.get("section"),"schedule_length_mm":entry.get("length_mm"),"geometry":None,"geometry_candidates":[],"reason":"no constrained member chain found"}; continue
        selected=ranked[0]
        if selected["label_distance_mm"]>max_label_distance:
            result[mark]={"backmark":mark,"section":entry.get("section"),"schedule_length_mm":entry.get("length_mm"),"geometry":None,"geometry_candidates":ranked,"reason":"best chain too far from designation"}; continue
        axis=segs[selected["seed_index"]]
        bolts=_chain_bolt_evidence(selected,segs,circles,member_search_radius)
        result[mark]={
            "backmark":mark,"section":entry.get("section"),"schedule_length_mm":entry.get("length_mm"),
            "geometry":{**asdict(axis),"length_mm":selected["length_mm"],"source_type":"CONSTRAINED_LINE_CHAIN","segment_indices":selected["segment_indices"]},
            "label_distance_mm":selected["label_distance_mm"],"length_error_mm":selected["length_error_mm"],
            "geometry_candidates":ranked,
            "assembly_circle_evidence":[asdict(b) for b in bolts],
            "evidence_status":"PASS" if selected["length_error_mm"]<=5 else "REVIEW",
        }
    return result

def aggregate_bolts(evidence):
    out=[]
    for mark,e in evidence.items():
        groups=defaultdict(int)
        for b in e.get("assembly_circle_evidence",[]): groups[(b["circle_diameter_mm"],b["mapped_nominal_diameter_mm"])] += 1
        for (circle,bolt),qty in sorted(groups.items()): out.append({"backmark":mark,"assembly_circle_diameter_mm":circle,"mapped_nominal_diameter_mm":bolt,"assembly_circle_count":qty})
    return out

def write_outputs(evidence,out_dir):
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    (out/"member_evidence.json").write_text(json.dumps(evidence,indent=2))
    rows=aggregate_bolts(evidence)
    with open(out/"bolt_evidence.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["backmark","assembly_circle_diameter_mm","mapped_nominal_diameter_mm","assembly_circle_count"]); w.writeheader(); w.writerows(rows)
    return rows
