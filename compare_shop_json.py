"""Standalone reference-vs-generated shop JSON regression comparator."""
from __future__ import annotations
import argparse
from shop_reporting import load_shop_json, load_shop_json_dir, compare_sets, write_comparison

def main():
    p=argparse.ArgumentParser(description='Compare existing shop drawing JSON against generated shop JSON')
    p.add_argument('--reference',required=True,help='Existing shop JSON file or directory')
    p.add_argument('--generated',required=True,help='Generated shop JSON file or directory')
    p.add_argument('--out',default='shop_json_compare')
    p.add_argument('--length-tol-mm',type=float,default=2.0)
    p.add_argument('--position-tol-mm',type=float,default=2.0)
    a=p.parse_args()
    ref=load_shop_json_dir(a.reference) if __import__('pathlib').Path(a.reference).is_dir() else load_shop_json(a.reference)
    gen=load_shop_json_dir(a.generated) if __import__('pathlib').Path(a.generated).is_dir() else load_shop_json(a.generated)
    result=compare_sets(ref,gen,a.length_tol_mm,a.position_tol_mm);write_comparison(result,a.out)
    print(result['status_counts'])

if __name__=='__main__': main()
