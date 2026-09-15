"""Count shop drawings identifiable and currently buildable from one assembly DXF."""
from __future__ import annotations
import argparse,json
import tower
from shop_reporting import inventory_from_schedule, write_inventory

def main():
    p=argparse.ArgumentParser(description='Shop drawing inventory for a specific assembly DXF')
    p.add_argument('--dxf',required=True);p.add_argument('--design-input');p.add_argument('--out',default='shop_inventory')
    a=p.parse_args(); schedule=tower.extract_member_schedule(a.dxf); inv=inventory_from_schedule(schedule,a.design_input);write_inventory(inv,a.out);print(json.dumps(inv,indent=2))
if __name__=='__main__':main()
