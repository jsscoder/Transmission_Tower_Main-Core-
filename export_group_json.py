"""Rebuild the group/backmark JSON package from an existing pipeline output directory."""
from __future__ import annotations
import argparse,json
from pathlib import Path
from shop_reporting import build_group_report

def read(name,out):
    p=Path(out)/name
    if not p.exists(): return None
    return json.loads(p.read_text(encoding='utf-8'))

def main():
    p=argparse.ArgumentParser();p.add_argument('--pipeline-out',required=True);a=p.parse_args();o=Path(a.pipeline_out)
    schedule=read('member_schedule.json',o) or read('member_schedule.csv',o) or {}
    # member_schedule.csv cannot be represented losslessly as schedule dict here;
    # normal runs already contain the group report, so this command is intended for
    # JSON-rich outputs.
    groups=read('connection_callout_groups.json',o) or []
    associations=read('connection_associations.json',o) or []
    topology=read('connection_topology.json',o) or {}
    resolved=read('resolved_connections.json',o) or {}
    inventory=read('shop_drawing_inventory.json',o) or {}
    comparison=read('shop_json_comparison.json',o)
    if isinstance(schedule,list):
        schedule={str(x.get("backmark")):x for x in schedule if isinstance(x,dict) and x.get("backmark") is not None}
    if not isinstance(schedule,dict): schedule={}
    report=build_group_report(o,schedule,groups,associations,topology,resolved,inventory,comparison);print(json.dumps({'groups':report['group_count'],'backmarks':report['backmark_count']},indent=2))
if __name__=='__main__':main()
