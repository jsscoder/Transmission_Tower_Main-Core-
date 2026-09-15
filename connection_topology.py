"""Joint-first topology model built from constrained member geometry."""
from __future__ import annotations
import csv,json,math
from pathlib import Path
from collections import defaultdict
from member_geometry import collect_segments, extract_member_geometry_candidates, dist

def _joint_cluster(points,tol=35.0):
    joints=[]
    refs=[]
    for mark,end,p in points:
        best=None
        for i,j in enumerate(joints):
            d=dist(p,(j["x"],j["y"]))
            if d<=tol and (best is None or d<best[0]): best=(d,i)
        if best is None:
            joints.append({"joint_id":f"J{len(joints)+1}","x":p[0],"y":p[1],"members":[]}); i=len(joints)-1; d=0
        else: d,i=best
        joints[i]["members"].append({"backmark":mark,"end":end,"distance_mm":round(d,2)})
        refs.append({"backmark":mark,"end":end,"joint_id":joints[i]["joint_id"]})
    return joints,refs

def _group_center(g):
    return (float(g["x"])+125.33,(float(g["y_min"])+float(g["y_max"])+71.39)/2.0)

def build_topology(dxf_path,schedule,member_evidence,groups,endpoint_tolerance=35.0,joint_group_radius=350.0):
    segs=collect_segments(dxf_path)
    geom_candidates=extract_member_geometry_candidates(dxf_path,schedule)
    assignments={}
    endpoint_points=[]
    for mark in schedule:
        ev=member_evidence.get(mark,{})
        # Prefer the Stage-4 selected chain because it includes evidence status.
        selected=None
        if ev.get("geometry") and ev.get("geometry",{}).get("segment_indices"):
            ids=ev["geometry"]["segment_indices"]
            # recover endpoint from candidate matching ids
            selected=next((c for c in geom_candidates.get(mark,{}).get("candidates",[]) if c.get("segment_indices")==ids),None)
        if selected is None: selected=geom_candidates.get(mark,{}).get("selected")
        if not selected: continue
        assignments[mark]={"segment_indices":selected["segment_indices"],"selected":selected,"geometry_evidence_status":ev.get("evidence_status","UNKNOWN")}
        ep=selected.get("endpoints")
        if ep:
            endpoint_points += [(mark,"A",(ep["A"]["x"],ep["A"]["y"])),(mark,"B",(ep["B"]["x"],ep["B"]["y"]))]
    joints,refs=_joint_cluster(endpoint_points,endpoint_tolerance)
    by_joint={r["joint_id"] for r in refs}
    group_proximity=[]
    for g in groups:
        gc=_group_center(g); near=[]
        for j in joints:
            d=dist(gc,(j["x"],j["y"]))
            if d<=joint_group_radius: near.append({"joint_id":j["joint_id"],"distance_mm":round(d,1)})
        near.sort(key=lambda x:x["distance_mm"])
        group_proximity.append({"group_id":g["group_id"],"nearby_joints":near})
    return {"member_assignments":assignments,"joints":joints,"endpoint_refs":refs,"group_joint_proximity":group_proximity,
            "stats":{"mapped_members":len(assignments),"joints":len(joints),"groups":len(groups),"groups_with_joint_candidates":sum(bool(x["nearby_joints"]) for x in group_proximity)}}

def write_topology(topology,out_dir):
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    (out/"connection_topology.json").write_text(json.dumps(topology,indent=2))
    rows=[]
    for j in topology["joints"]:
        for m in j["members"]: rows.append({"joint_id":j["joint_id"],"joint_x":j["x"],"joint_y":j["y"],**m})
    with open(out/"member_joint_map.csv","w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=["joint_id","joint_x","joint_y","backmark","end","distance_mm"]);w.writeheader();w.writerows(rows)
    return topology
