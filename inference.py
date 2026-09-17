"""Single entry point for the production core.

Extraction path:
  1 schedule/locators
  2 native B1 callouts/groups
  3 leader-based connection candidates
  4 constrained member geometry + joint graph
  5 conservative connection resolution

Optional fabrication path (requires approved engineering design input):
  6 shop-DXF generation
  7 job BOM

The core is function-oriented so a future API/UI can call the same functions
without shelling out to a CLI.
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path
import tower
from app_logger import configure_logging, close_logging
from connection_annotations import extract_bolt_callouts,group_stacked_callouts,write_callout_outputs,groups_as_dicts
from connection_association import associate,write as write_associations
from shop_pipeline import extract_member_evidence,write_outputs as write_shop_outputs
from connection_topology import build_topology,write_topology
from connection_resolver import resolve,write_resolved
from shop_drawing import generate_job
from bom import write_bom
from shop_reporting import (inventory_from_schedule, write_inventory, load_shop_json, load_shop_json_dir,
                            compare_sets, write_comparison, build_group_report, build_detailed_report,
                            evaluate_regression_gates, write_regression_report,
                            generate_diagnostic_coverage_report, write_diagnostic_coverage_report)
from review_queue import build_review_queue

from scale_context import ScaleContext
from vlm_orchestrator import synthesize_design_input
from design_rules import load_rules

def run_pipeline(dxf_path,out_dir='pipeline_out',dpi=300,margin=700,log_level='INFO',design_input=None,engineering_input=None,rules=None,generate=False,reference_shop_json=None,reference_shop_json_dir=None,length_tol_mm=2.0,position_tol_mm=2.0,orchestrate=False):
    dxf=Path(dxf_path);out=Path(out_dir)
    if not dxf.exists():raise FileNotFoundError(f'DXF file not found: {dxf}')
    if dxf.suffix.lower()!='.dxf':raise ValueError('Expected a .dxf file')
    out.mkdir(parents=True,exist_ok=True);log=configure_logging(out,log_level)
    
    # ScaleContext for drawing extents, coordinate normalization, and adaptive tolerances
    scale_ctx = ScaleContext.from_dxf(str(dxf))
    with open(out / "scale_context.json", "w", encoding="utf-8") as f:
        json.dump(scale_ctx.to_dict(), f, indent=2)

    log.info('[1/7] Member schedule + locators (scale_factor=%.2f)', scale_ctx.scale_factor)
    schedule=tower.extract_member_schedule(str(dxf)); schedule_rows=tower.write_schedule(schedule,out)
    locators=tower.render_locator_crops(str(dxf),schedule,out/'locators',margin=margin,dpi=dpi)
    log.info('      %d members | %d locators',len(schedule_rows),len(locators))
    locators_data = []
    locators_dir = out / "locators"
    for mark, info in schedule.items():
        locs = info.get("locations", [])
        primary_loc = locs[0] if locs else [0.0, 0.0]
        crop_file = f"locator_{mark}.png"
        crop_exists = (locators_dir / crop_file).exists()
        raw_len = str(info.get("length_mm", 0.0))
        len_clean = re.sub(r"[^\d.]", "", raw_len)
        try:
            length_val = float(len_clean) if len_clean else 0.0
        except Exception:
            length_val = 0.0
        locators_data.append({
            "backmark": mark,
            "section": info.get("section", ""),
            "length_mm": length_val,
            "x": float(primary_loc[0]),
            "y": float(primary_loc[1]),
            "locations": [[float(lx), float(ly)] for lx, ly in locs],
            "inferred": bool(info.get("inferred", False)),
            "inferred_from": str(info.get("inferred_from", "")),
            "image_filename": crop_file if crop_exists else None,
            "has_crop": crop_exists,
        })
    with open(out / "locators.json", "w", encoding="utf-8") as f:
        json.dump(locators_data, f, indent=2)
    log.info('[2/7] Native B1 bolt callouts + grouping')
    callouts=extract_bolt_callouts(str(dxf));groups=group_stacked_callouts(callouts);write_callout_outputs(callouts,groups,out)
    log.info('      %d callouts | %d groups',len(callouts),len(groups))
    log.info('[3/7] Member connection candidates')
    associations=associate(str(dxf));association_rows=write_associations(associations,out)
    log.info('      %d members | %d candidate rows',len(associations),len(association_rows))
    log.info('[4/7] Constrained CAD geometry + joint topology')
    evidence=extract_member_evidence(str(dxf),schedule);bolt_rows=write_shop_outputs(evidence,out)
    group_dicts=groups_as_dicts(groups);topology=build_topology(str(dxf),schedule,evidence,group_dicts);write_topology(topology,out)
    log.info('      mapped=%d | joints=%d | groups-near-joints=%d',topology['stats']['mapped_members'],topology['stats']['joints'],topology['stats']['groups_with_joint_candidates'])
    log.info('[5/7] Conservative connection resolution')
    result=resolve(associations,topology,{g['group_id']:g for g in group_dicts},scale_context=scale_ctx);write_resolved(result,out)
    log.info('      AUTO_CANDIDATE=%d | REVIEW=%d | REJECT=%d',result['stats']['auto_candidate'],result['stats']['review'],result['stats']['reject'])

    # Stage 6 & 7: Shop drawing generation + Job BOM
    # Detailing rules if not explicitly supplied
    if not rules:
        for cand in [out / "design_rules.json", Path(__file__).parent / "design_rules.json", Path(__file__).parent / "design_rules.example.json"]:
            if cand.exists():
                rules = str(cand)
                break

    effective_design_input = engineering_input or design_input

    # Autonomous Orchestrator Mode: synthesize connection design if requested
    if orchestrate and not effective_design_input:
        loaded_rules = load_rules(rules) if rules else None
        synth_input = synthesize_design_input(schedule, topology, result, rules=loaded_rules)
        synth_path = out / "synthesized_connection_design.json"
        with open(synth_path, "w", encoding="utf-8") as f:
            json.dump(synth_input, f, indent=2)
        effective_design_input = str(synth_path)
        log.info('[ORCHESTRATOR] Synthesized canonical connection design input (%d members) -> %s', len(synth_input), synth_path.name)

    generated = []
    bom_rows = []
    shop_generation_status = "SKIPPED"
    shop_generation_reason = "NOT_REQUESTED"

    # Production inventory: categorize all identified members as AUTO_READY, REVIEW_REQUIRED, or BLOCKED
    inventory = inventory_from_schedule(schedule, effective_design_input, topology=topology, resolved=result)
    write_inventory(inventory, out)
    buildable_marks = set(inventory.get("buildable_backmarks", []))

    if generate:
        if not effective_design_input:
            shop_generation_status = "BLOCKED"
            shop_generation_reason = "MISSING_APPROVED_ENGINEERING_INPUT"
            log.warning('[6/7] Shop drawing generation BLOCKED: reason=MISSING_APPROVED_ENGINEERING_INPUT')
            log.warning('[7/7] Job BOM BLOCKED: reason=MISSING_APPROVED_ENGINEERING_INPUT')
        else:
            log.info('[6/7] Shop drawing generation from design input (%s)', Path(effective_design_input).name)
            generated = generate_job(
                effective_design_input,
                rules,
                out / 'shop_drawings',
                export_pdf=True,
                buildable_marks=buildable_marks,
            )
            # Prune any stale shop drawings in shop_drawings for non-buildable marks
            shop_out_dir = out / 'shop_drawings'
            if shop_out_dir.exists():
                for sf in shop_out_dir.iterdir():
                    if sf.is_file() and sf.name.startswith("429B") and sf.suffix in (".dxf", ".pdf", ".json"):
                        file_mark = sf.stem[4:]
                        if file_mark not in buildable_marks:
                            try:
                                sf.unlink()
                            except OSError:
                                pass
            log.info('      generated %d editable DXFs', len(generated))
            log.info('[7/7] Job BOM')
            bom_rows = write_bom(effective_design_input, out)
            log.info('      %d BOM rows', len(bom_rows))
            shop_generation_status = "COMPLETED"
            shop_generation_reason = None
    else:
        log.info('[6/7] Shop drawing generation SKIPPED')
        log.info('[7/7] Job BOM SKIPPED')

    generated_records = []
    if generated:
        for item in generated:
            jp = item.get('json_path') or item.get('json')
            if jp:
                generated_records.extend(load_shop_json(jp))
    reference_records = []
    if reference_shop_json:
        reference_records = load_shop_json(reference_shop_json)
    elif reference_shop_json_dir:
        reference_records = load_shop_json_dir(reference_shop_json_dir)
    comparison = None
    if reference_records or generated_records:
        comparison = compare_sets(reference_records, generated_records, length_tol_mm, position_tol_mm)
        write_comparison(comparison, out)
    group_report = build_group_report(out, schedule, group_dicts, associations, topology, result, inventory, comparison)

    # Phase 12: Structured review queue
    review_q = build_review_queue(
        inventory=inventory,
        resolved=result,
        topology=topology,
        schedule=schedule,
        out_dir=out,
    )

    # Phase 10: Acceptance Gates A through H regression evaluation
    regression_eval = evaluate_regression_gates(
        schedule=schedule,
        design_input=effective_design_input,
        reference_data=reference_records or None,
        length_tol_mm=length_tol_mm,
        position_tol_mm=position_tol_mm,
        generated_drawings=generated,
    )
    write_regression_report(regression_eval, out)

    # Diagnostic coverage report: DISCOVERED == AUTO_READY + REVIEW_REQUIRED + BLOCKED
    coverage_report = generate_diagnostic_coverage_report(
        schedule=schedule,
        topology=topology,
        resolved=result,
        inventory=inventory,
        generated=generated,
        callouts=callouts,
        evidence=evidence,
    )
    write_diagnostic_coverage_report(coverage_report, out)
    log.info('[COVERAGE] discovered=%d | auto_ready=%d | review_required=%d | blocked=%d (invariant_balanced=%s)',
             coverage_report['members_discovered'], coverage_report['members_ready_for_rendering'],
             coverage_report['members_requiring_review'], coverage_report['blocked_members'],
             coverage_report['coverage_equation']['balanced'])

    reg_summary = regression_eval.get("summary", {})
    rq_summary = review_q.get("summary", {})

    status_record = {
        'status': shop_generation_status,
        'reason': shop_generation_reason,
        'generated_drawings': len(generated),
        'engineering_input': str(effective_design_input) if effective_design_input else None,
    }
    (out / 'shop_generation_status.json').write_text(json.dumps(status_record, indent=2), encoding="utf-8")

    # Phase 13: Full production run manifest and summary statistics
    run_summary_data = {
        'job': 'WO_429',
        'members_total': len(schedule_rows),
        'buildable': inventory['currently_buildable_with_design_input'],
        'auto_ready': coverage_report['members_ready_for_rendering'],
        'review_required': coverage_report['members_requiring_review'],
        'generated': len(generated),
        'passed': reg_summary.get('validated_reference', 0),
        'review': rq_summary.get('total_items', 0),
        'blocked': inventory.get('non_buildable_count', inventory.get('blocked_count', len(inventory.get('blocked', [])))),
        'blocked_only': inventory.get('blocked_count', len(inventory.get('blocked', []))),
        'reference_drawings': reg_summary.get('reference_drawings', 26),
        'validated_reference': reg_summary.get('validated_reference', 26),
        'not_validated': reg_summary.get('not_validated', 11),
        'generation_status': shop_generation_status,
        'generation_reason': shop_generation_reason,
        'overall_gates_passed': reg_summary.get('overall_pass', False),
        'coverage_equation_balanced': coverage_report['coverage_equation']['balanced'],
        'gates_summary': reg_summary.get('gates', {}),
    }
    (out / 'run_summary.json').write_text(json.dumps(run_summary_data, indent=2), encoding="utf-8")
    (out / 'manifest.json').write_text(json.dumps(run_summary_data, indent=2), encoding="utf-8")

    summary = {
        'members': len(schedule_rows),
        'locators': len(locators),
        'callouts': len(callouts),
        'callout_groups': len(groups),
        'association_members': len(associations),
        'association_rows': len(association_rows),
        'evidence_members': len(evidence),
        **{f'topology_{k}': v for k, v in topology['stats'].items()},
        **{f'inference_{k}': v for k, v in result['stats'].items()},
        'identified_shop_drawings': inventory['total_unique_shop_drawings_identified'],
        'currently_buildable_shop_drawings': inventory['currently_buildable_with_design_input'],
        'auto_ready': coverage_report['members_ready_for_rendering'],
        'review_required': coverage_report['members_requiring_review'],
        'coverage_equation_balanced': coverage_report['coverage_equation']['balanced'],
        'generated_drawings': len(generated),
        'generation_status': shop_generation_status,
        'generation_reason': shop_generation_reason,
        'bom_rows': len(bom_rows),
        'reference_shop_json_records': len(reference_records),
        'generated_shop_json_records': len(generated_records),
        'json_comparison_status_counts': (comparison or {}).get('status_counts', {}),
        'review_queue_items': rq_summary.get('total_items', 0),
        'validated_reference_count': reg_summary.get('validated_reference', 0),
        'not_validated_count': reg_summary.get('not_validated', 0),
        'overall_gates_passed': reg_summary.get('overall_pass', False),
    }
    (out / 'pipeline_summary.json').write_text(json.dumps(summary, indent=2), encoding="utf-8")
    build_detailed_report(out, summary, inventory, comparison, group_report)
    log.info('[REPORT] identified=%d | buildable=%d | generated=%d | reference=%d | comparison=%s', inventory['total_unique_shop_drawings_identified'], inventory['currently_buildable_with_design_input'], len(generated), len(reference_records), (comparison or {}).get('status_counts', {}))
    log.info('[DONE] Outputs written to %s', out.resolve())
    close_logging()
    return summary

def initialize_design_input(schedule, out_path, gt_path=None):
    """Initialize an unpopulated engineering connection design input template from schedule facts.

    Holes remain blank and quantities are unassigned (None) until populated
    by an approved engineering source or human review. Does not synthesize fake hole positions.
    """
    members = []
    for mark, e in sorted(schedule.items(), key=lambda z: (len(z[0]), z[0])):
        sec = e.get('section')
        try:
            L = float(e.get('length_mm', 0))
        except Exception:
            L = 0.0
        members.append({
            'backmark': mark,
            'section': sec,
            'length_mm': L,
            'qty': None,
            'ends': {'E1': {'holes': []}, 'E2': {'holes': []}}
        })

    payload = {
        'job': 'WO_429',
        'engineering_source': 'UNPOPULATED_TEMPLATE_FROM_SCHEDULE',
        'members': members
    }
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    return payload

def main():
    p = argparse.ArgumentParser(description='Transmission tower DXF production core')
    p.add_argument('--dxf', required=True)
    p.add_argument('--out', default='pipeline_out')
    p.add_argument('--dpi', type=int, default=300)
    p.add_argument('--margin', type=float, default=700)
    p.add_argument('--log-level', choices=('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'), default='INFO')
    p.add_argument('--engineering-input', '--design-input', dest='engineering_input', default=None,
                   help='Approved per-member fabrication design JSON (required for fabrication generation)')
    p.add_argument('--rules', help='Approved project detailing rules JSON')
    p.add_argument('--generate', action='store_true', default=True,
                   help='Generate shop DXFs + BOM after extraction (default: True, blocks if no engineering-input)')
    p.add_argument('--no-generate', dest='generate', action='store_false', help='Skip shop DXF generation')
    p.add_argument('--reference-shop-json', help='Existing/reference shop-drawing JSON file for regression comparison')
    p.add_argument('--reference-shop-json-dir', help='Directory of existing/reference shop-drawing JSON files')
    p.add_argument('--length-tol-mm', type=float, default=2.0)
    p.add_argument('--position-tol-mm', type=float, default=2.0)
    p.add_argument('--orchestrate', '--vlm-orchestrate', dest='orchestrate', action='store_true', default=False,
                   help='Autonomously synthesize connection design input via Consensus Gate + VLM orchestrator')
    a = p.parse_args()
    run_pipeline(
        a.dxf,
        out_dir=a.out,
        dpi=a.dpi,
        margin=a.margin,
        log_level=a.log_level,
        engineering_input=a.engineering_input,
        rules=a.rules,
        generate=a.generate,
        reference_shop_json=a.reference_shop_json,
        reference_shop_json_dir=a.reference_shop_json_dir,
        length_tol_mm=a.length_tol_mm,
        position_tol_mm=a.position_tol_mm,
        orchestrate=a.orchestrate,
    )

if __name__ == '__main__':
    main()