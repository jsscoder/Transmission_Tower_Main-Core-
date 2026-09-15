"""Associate native bolt-callout annotations with member-designation anchors.

Association is deliberately evidence-first: the output contains candidate
connection ends and nearby bolt callout groups with distances/confidence. It
never hard-codes a backmark -> bolt pattern mapping.
"""
from __future__ import annotations
import json, math, re
from pathlib import Path
from collections import defaultdict, deque
import ezdxf
from ezdxf import recover
from connection_annotations import group_stacked_callouts, extract_bolt_callouts

DESIGNATION_LAYER='23_Member designation'
BACKMARK_RE=re.compile(r'^\d{1,4}[A-Z]{0,2}$')


def _designation_texts(msp):
    marks=[]; desc=[]
    for e in msp.query('TEXT'):
        if e.dxf.layer!=DESIGNATION_LAYER: continue
        t=e.dxf.text.strip(); p=(float(e.dxf.insert.x),float(e.dxf.insert.y))
        if BACKMARK_RE.fullmatch(t): marks.append((t,p))
        elif '..' in t: desc.append((t,p))
    return marks,desc


def _pair_descriptions(marks, descs):
    used=set(); pairs={}
    for dt,dp in descs:
        best=None
        for i,(mark,mp) in enumerate(marks):
            if i in used: continue
            d=math.dist(dp,mp)
            if best is None or d<best[0]: best=(d,i,mark,mp)
        if best:
            used.add(best[1]); pairs[best[2]]=(dt,dp,best[0])
    return pairs


def _designation_lines(msp):
    adj=defaultdict(list); edges=[]
    for e in msp.query('LINE'):
        if e.dxf.layer!=DESIGNATION_LAYER: continue
        a=(round(e.dxf.start.x,1),round(e.dxf.start.y,1));b=(round(e.dxf.end.x,1),round(e.dxf.end.y,1))
        adj[a].append(b);adj[b].append(a);edges.append((a,b))
    return adj,edges

def _point_segment_distance(p,a,b):
    dx=b[0]-a[0];dy=b[1]-a[1];den=dx*dx+dy*dy
    t=((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den if den else 0.0
    t=max(0.0,min(1.0,t));q=(a[0]+t*dx,a[1]+t*dy)
    return math.dist(p,q)

def _leader_anchors(desc_point, adj, edges, max_seed_distance=260):
    if not edges: return []
    # Seed from the nearest DESIGNATION line segment, rather than its nearest
    # endpoint. This matters when the text sits beside the middle of a leader.
    ranked=sorted((_point_segment_distance(desc_point,a,b),a,b) for a,b in edges)
    sd,a0,b0=ranked[0]
    if sd>max_seed_distance: return []
    # Build the component containing the seed edge.
    seen={a0,b0}; q=deque([a0,b0])
    while q:
        u=q.popleft()
        for v in adj[u]:
            if v not in seen: seen.add(v);q.append(v)
    endpoints=[p for p in seen if len([v for v in adj[p] if v in seen])==1]
    endpoints.sort(key=lambda p:math.dist(desc_point,p))
    # Leader components often include a short horizontal underline at the
    # description. Remove the endpoint that is effectively the text-side end.
    if endpoints and math.dist(desc_point,endpoints[0]) < max(150.0, sd+100.0):
        endpoints=endpoints[1:]
    return [{"x":p[0],"y":p[1],"distance_from_description_mm":round(math.dist(desc_point,p),1)} for p in endpoints]

def _rect_distance(point, group, width=250.66, height=71.39):
    px,py=point; xmin,xmax=group['x'],group['x']+width; ymin,ymax=group['y_min'],group['y_max']+height
    dx=max(xmin-px,0,px-xmax);dy=max(ymin-py,0,py-ymax)
    return math.hypot(dx,dy)


def associate(dxf_path, radius=450):
    doc,_=recover.readfile(dxf_path);msp=doc.modelspace()
    marks,descs=_designation_texts(msp);pairs=_pair_descriptions(marks,descs)
    adj,edges=_designation_lines(msp)
    groups=[{
        'group_id':i, 'x':g.x, 'y_min':g.y_min, 'y_max':g.y_max,
        'callouts':g.callouts,
        'bolt_quantity_by_diameter':dict(sorted({d:sum(c['quantity'] for c in g.callouts if c['nominal_diameter_mm']==d) for d in {c['nominal_diameter_mm'] for c in g.callouts}}.items()))
    } for i,g in enumerate(group_stacked_callouts(extract_bolt_callouts(dxf_path)))]
    result={}
    for mark,(dt,dp,pair_d) in pairs.items():
        anchors=_leader_anchors(dp,adj,edges)
        # If no explicit leader, use the backmark itself as a weak anchor.
        if not anchors:
            mp=next(p for mm,p in marks if mm==mark)
            anchors=[{'x':mp[0],'y':mp[1],'distance_from_description_mm':None,'fallback':True}]
        ends=[]
        for ai,a in enumerate(anchors):
            p=(a['x'],a['y'])
            near=[]
            for g in groups:
                d=_rect_distance(p,g)
                if d<=radius:
                    near.append({**g,'distance_to_anchor_mm':round(d,1)})
            near.sort(key=lambda z:z['distance_to_anchor_mm'])
            ends.append({'end_id':f'{mark}_E{ai+1}','anchor':a,'nearby_callout_groups':near})
        result[mark]={
            'backmark':mark,
            'designation_text':dt,
            'designation_description_position':{'x':dp[0],'y':dp[1]},
            'description_match_distance_mm':round(pair_d,1),
            'connection_ends':ends,
        }
    return result


def write(result,out_dir):
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    (out/'member_connection_candidates.json').write_text(json.dumps(result,indent=2))
    rows=[]
    for mark,e in result.items():
        for end in e['connection_ends']:
            for g in end['nearby_callout_groups']:
                rows.append({
                    'backmark':mark,'connection_end':end['end_id'],
                    'anchor_x':end['anchor']['x'],'anchor_y':end['anchor']['y'],
                    'group_id':g['group_id'],'group_x':g['x'],'group_y':g['y_min'],
                    'distance_mm':g['distance_to_anchor_mm'],
                    'bolt_summary':json.dumps(g['bolt_quantity_by_diameter'],sort_keys=True),
                    'raw_callouts':' | '.join(c['raw'] for c in g['callouts'])
                })
    import csv
    with open(out/'member_connection_candidates.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['backmark']);w.writeheader();w.writerows(rows)
    return rows
