"""Joint-first connection resolver.

Important limitation: an assembly callout can describe a joint shared by
several members. Therefore this resolver may identify a likely joint/group,
but it does NOT split a joint's bolt count among members unless an explicit
allocation source is supplied. That prevents a common and dangerous failure:
copying the full joint bolt group onto every member.
"""
from __future__ import annotations
import csv,json,math
from collections import defaultdict
from pathlib import Path

def _score_distance(d,scale): return math.exp(-max(0,float(d))/scale)
def _group_center(g): return (float(g['x'])+125.33,(float(g['y_min'])+float(g['y_max'])+71.39)/2)
def _cos_score(a,b,c):
    # alignment of callout vector with member vector; absolute because drawing
    # annotations may sit on either side of an endpoint.
    vx=b[0]-a[0];vy=b[1]-a[1]; ux=c[0]-a[0];uy=c[1]-a[1]
    nv=math.hypot(vx,vy);nu=math.hypot(ux,uy)
    return .5 if not nv or not nu else min(1,abs((vx*ux+vy*uy)/(nv*nu)))

def resolve(candidates,topology,groups_by_id,auto_threshold=.82,review_threshold=.45,scale_context=None):
    refs={(r['backmark'],r['end']):r['joint_id'] for r in topology.get('endpoint_refs',[])}
    joints={j['joint_id']:(j['x'],j['y']) for j in topology.get('joints',[])}
    gm={r['group_id']:r.get('nearby_joints',[]) for r in topology.get('group_joint_proximity',[])}
    joint_decay = scale_context.joint_decay_scale_mm if scale_context else 180.0
    anchor_decay = scale_context.anchor_decay_scale_mm if scale_context else 140.0
    proposals=[]
    for mark,entry in candidates.items():
        ass=topology.get('member_assignments',{}).get(mark)
        if not ass: continue
        selected=ass.get('selected',{}); ep=selected.get('endpoints',{})
        if not ep: continue
        pts={'A':(ep['A']['x'],ep['A']['y']),'B':(ep['B']['x'],ep['B']['y'])}
        for endrec in entry.get('connection_ends',[]):
            end_id=endrec['end_id']; anchor=endrec.get('anchor',{}); ap=(anchor.get('x'),anchor.get('y'))
            if ap[0] is None: continue
            # End identity comes from geometry, not from association order.
            end=min(pts,key=lambda k:math.hypot(ap[0]-pts[k][0],ap[1]-pts[k][1]))
            joint=refs.get((mark,end))
            other='B' if end=='A' else 'A'
            for g in endrec.get('nearby_callout_groups',[]):
                gid=g['group_id']; gc=_group_center(g)
                d_anchor=float(g.get('distance_to_anchor_mm',99999)); d_joint=99999
                for x in gm.get(gid,[]):
                    if x['joint_id']==joint: d_joint=float(x['distance_mm']); break
                if joint and d_joint<99999:
                    sj=_score_distance(d_joint,joint_decay)
                else: sj=0
                sd=_score_distance(d_anchor,anchor_decay)
                sdir=_cos_score(pts[end],pts[other],gc)
                # Candidate is strong only when both the joint and annotation
                # support the same endpoint. Direction is a tie breaker.
                score=.50*sj+.35*sd+.15*sdir
                proposals.append({'backmark':mark,'end_id':end_id,'geometry_end':end,'joint_id':joint,'group_id':gid,'score':round(score,4),'joint_score':round(sj,4),'anchor_score':round(sd,4),'direction_score':round(sdir,4),'raw_callouts':[c['raw'] for c in g.get('callouts',[])], 'bolt_quantity_by_diameter':g.get('bolt_quantity_by_diameter',{})})
    by_end=defaultdict(list)
    for p in proposals: by_end[(p['backmark'],p['end_id'])].append(p)
    for rows in by_end.values(): rows.sort(key=lambda x:x['score'],reverse=True)
    # Compete only within the same physical joint first. A group may still be
    # reused at another joint only with explicit design allocation later.
    owners={}
    decisions=[]; members=defaultdict(lambda:{'connection_ends':[]})
    for key,rows in by_end.items():
        best=rows[0]; second=rows[1]['score'] if len(rows)>1 else 0; margin=best['score']-second
        same_joint=[p for p in proposals if p['group_id']==best['group_id'] and p['joint_id']==best['joint_id']]
        competing=any((p['backmark'],p['end_id'])!=key for p in same_joint)
        if competing: status='REVIEW'
        elif best['score']>=auto_threshold and margin>=.10: status='AUTO_CANDIDATE'
        elif best['score']>=review_threshold: status='REVIEW'
        else: status='REJECT'
        # AUTO_CANDIDATE is deliberately not a fabrication authorization.
        rec={**best,'status':status,'margin':round(margin,4),'competing_owner':competing}
        decisions.append(rec)
        members[key[0]]['connection_ends'].append({'end_id':key[1],'geometry_end':best['geometry_end'],'joint_id':best['joint_id'],'status':status,'confidence':best['score'],'assigned_group_id':None,'candidate_group_id':best['group_id'],'candidate_count':len(rows),'candidates':rows[:5]})
    return {'members':dict(members),'decisions':decisions,'stats':{'candidate_proposals':len(proposals),'auto_candidate':sum(x['status']=='AUTO_CANDIDATE' for x in decisions),'review':sum(x['status']=='REVIEW' for x in decisions),'reject':sum(x['status']=='REJECT' for x in decisions),'fabrication_assignments':0}}

def write_resolved(result,out_dir):
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True); (out/'resolved_connections.json').write_text(json.dumps(result,indent=2))
    fields=['backmark','end_id','geometry_end','joint_id','status','confidence','candidate_group_id','candidate_count']
    with open(out/'resolved_member_end_bom.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for m,e in result['members'].items():
            for r in e['connection_ends']:w.writerow({'backmark':m,**{k:r.get(k) for k in fields[1:]}})
    with open(out/'connection_review.csv','w',newline='') as f:
        fields2=['backmark','end_id','geometry_end','joint_id','group_id','status','score','margin','competing_owner','raw_callouts'];w=csv.DictWriter(f,fieldnames=fields2);w.writeheader();w.writerows({k:d.get(k) for k in fields2} for d in result['decisions'])
    return result
