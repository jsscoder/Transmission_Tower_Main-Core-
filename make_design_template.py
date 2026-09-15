"""Create a fabrication-design input template from an assembly DXF.

This only copies Stage-1 facts (backmark, section, length, quantity). Ends and
holes remain blank because those values must come from an approved engineering
source or human review.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import tower

def make_template(dxf,out):
    schedule=tower.extract_member_schedule(dxf); members=[]
    for mark,e in sorted(schedule.items()):
        members.append({'backmark':mark,'section':e.get('section'),'length_mm':e.get('length_mm'),'qty':None,'schedule_callout_count':e.get('count',0),'ends':{'E1':{'holes':[]},'E2':{'holes':[]}}})
    payload={'job':'REPLACE_WITH_JOB_ID','source_assembly':str(Path(dxf).name),'engineering_source':'REQUIRED — do not leave blank for production','members':members}
    Path(out).write_text(json.dumps(payload,indent=2));return payload
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dxf',required=True);p.add_argument('--out',default='connection_design.template.json');a=p.parse_args();make_template(a.dxf,a.out);print(a.out)
