"""Real Engine Output Adapter: Discovers, parses, and normalizes output files produced by the Python engine."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Optional

from backend.schemas import (
    BOMItem,
    CandidateItem,
    LocatorItem,
    MemberItem,
    PipelineMetrics,
    RegressionDrawingScore,
    ReviewItem,
    ShopDrawingItem,
    TopologyJoint,
)


class EngineAdapter:
    def __init__(self, output_dir: Path | str):
        self.output_dir = Path(output_dir)

    def exists(self) -> bool:
        return self.output_dir.exists()

    def get_metrics(self) -> PipelineMetrics:
        """Parse pipeline_summary.json, run_summary.json, and regression_report.json."""
        pipe_sum_file = self.output_dir / "pipeline_summary.json"
        run_sum_file = self.output_dir / "run_summary.json"
        reg_file = self.output_dir / "regression_4_drawings.json"

        pipe_data: dict[str, Any] = {}
        if pipe_sum_file.exists():
            try:
                pipe_data = json.loads(pipe_sum_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        run_data: dict[str, Any] = {}
        if run_sum_file.exists():
            try:
                run_data = json.loads(run_sum_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        reg_data: dict[str, Any] = {}
        if reg_file.exists():
            try:
                reg_data = json.loads(reg_file.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Also count shop drawings in folder
        shop_dir = self.output_dir / "shop_drawings"
        dxf_count = len(list(shop_dir.glob("*.dxf"))) if shop_dir.exists() else 0

        # Also count BOM rows
        bom_file = self.output_dir / "job_bom.json"
        bom_rows = 0
        if bom_file.exists():
            try:
                bom_json = json.loads(bom_file.read_text(encoding="utf-8"))
                if isinstance(bom_json, list):
                    bom_rows = len(bom_json)
                elif isinstance(bom_json, dict):
                    bom_rows = len(bom_json.get("members", bom_json.get("rows", [])))
            except Exception:
                pass

        # Determine golden gate status
        golden_status = reg_data.get("gate_status", "N/A")
        if golden_status == "N/A" and run_data.get("overall_gates_passed"):
            golden_status = "PASS"

        return PipelineMetrics(
            members_total=pipe_data.get("members", run_data.get("members_total", 0)),
            locators_count=pipe_data.get("locators", pipe_data.get("members", 0)),
            callouts_count=pipe_data.get("bolt_callouts", 0),
            callout_groups=pipe_data.get("callout_groups", 0),
            joints_count=pipe_data.get("joints", 0),
            topology_groups=pipe_data.get("topology_groups", 0),
            connection_candidates=pipe_data.get("candidates", 0),
            auto_allocated=pipe_data.get("AUTO", 0),
            review_count=pipe_data.get("REVIEW", 0),
            reject_count=pipe_data.get("REJECT", 0),
            blocked_count=run_data.get("blocked", 0),
            buildable_count=run_data.get("buildable", 0),
            generated_drawings=dxf_count,
            bom_rows=bom_rows,
            golden_gate_status=golden_status,
            reference_drawings_passed=run_data.get("validated_reference", 0),
        )

    def get_members(self) -> list[MemberItem]:
        """Parse member_schedule.json/csv cross-referenced with drawing_inventory and topology."""
        sched_json = self.output_dir / "member_schedule.json"
        inv_json = self.output_dir / "drawing_inventory.json"
        topo_json = self.output_dir / "connection_topology.json"

        inventory_status: dict[str, dict] = {}
        if inv_json.exists():
            try:
                inv_data = json.loads(inv_json.read_text(encoding="utf-8"))
                for item in inv_data:
                    bm = str(item.get("backmark", "")).strip()
                    inventory_status[bm] = item
            except Exception:
                pass

        mapped_members: set[str] = set()
        if topo_json.exists():
            try:
                topo_data = json.loads(topo_json.read_text(encoding="utf-8"))
                mapped_members = set(topo_data.get("mapped_members", []))
            except Exception:
                pass

        items: list[MemberItem] = []
        if sched_json.exists():
            try:
                data = json.loads(sched_json.read_text(encoding="utf-8"))
                for m in data:
                    bm = str(m.get("backmark", "")).strip()
                    inv = inventory_status.get(bm, {})
                    reasons = inv.get("reasons", [])
                    if isinstance(reasons, str) and reasons:
                        reasons = [reasons]

                    drawing_status = inv.get("status", "BLOCKED")
                    topo_status = "MAPPED" if bm in mapped_members else "UNMAPPED"

                    items.append(
                        MemberItem(
                            backmark=bm,
                            section=str(m.get("section", "")),
                            canonical_section=m.get("canonical_section"),
                            section_family=m.get("section_family"),
                            length_mm=float(m.get("length_mm", 0.0)),
                            quantity=int(m.get("count", m.get("qty", 1))) if m.get("count") or m.get("qty") else None,
                            geometry_status="EXTRACTED",
                            topology_status=topo_status,
                            inference_status="AUTO" if drawing_status == "BUILDABLE" else "REVIEW",
                            drawing_status=drawing_status,
                            bom_status="VALIDATED" if drawing_status == "BUILDABLE" else "BLOCKED",
                            reasons=reasons,
                        )
                    )
            except Exception:
                pass

        return items

    def get_locators(self) -> list[LocatorItem]:
        """Parse bolt_callouts.json or locators directory."""
        callouts_file = self.output_dir / "bolt_callouts.json"
        items: list[LocatorItem] = []
        if callouts_file.exists():
            try:
                data = json.loads(callouts_file.read_text(encoding="utf-8"))
                for c in data:
                    items.append(
                        LocatorItem(
                            member_backmark=str(c.get("text", "")),
                            source_entity="TEXT",
                            x=float(c.get("center", [0, 0])[0] if isinstance(c.get("center"), (list, tuple)) else 0.0),
                            y=float(c.get("center", [0, 0])[1] if isinstance(c.get("center"), (list, tuple)) else 0.0),
                            confidence=1.0,
                            associated_member=c.get("layer"),
                            association_status="CLUSTERED",
                        )
                    )
            except Exception:
                pass
        return items

    def get_topology(self) -> list[TopologyJoint]:
        """Parse connection_topology.json into joint graph nodes."""
        topo_file = self.output_dir / "connection_topology.json"
        joints: list[TopologyJoint] = []
        if topo_file.exists():
            try:
                data = json.loads(topo_file.read_text(encoding="utf-8"))
                for j_id, j_data in data.get("joints", {}).items():
                    coord = j_data.get("coord", [0.0, 0.0])
                    members = [f"{m['member']}_{m.get('end', '')}" for m in j_data.get("members", [])]
                    groups = [str(g) for g in j_data.get("callout_groups", [])]
                    joints.append(
                        TopologyJoint(
                            joint_id=str(j_id),
                            x=float(coord[0]) if len(coord) > 0 else 0.0,
                            y=float(coord[1]) if len(coord) > 1 else 0.0,
                            connected_members=members,
                            callout_groups=groups,
                        )
                    )
            except Exception:
                pass
        return joints

    def get_inference(self) -> list[CandidateItem]:
        """Parse member_connection_candidates.json."""
        cand_file = self.output_dir / "member_connection_candidates.json"
        items: list[CandidateItem] = []
        if cand_file.exists():
            try:
                data = json.loads(cand_file.read_text(encoding="utf-8"))
                for c in data:
                    items.append(
                        CandidateItem(
                            member=str(c.get("member", "")),
                            end=str(c.get("end", "")),
                            group_id=str(c.get("group_id", "")),
                            callout_text=str(c.get("callout_text", "")),
                            bolt_type=str(c.get("bolt_type", "")),
                            bolt_count=int(c.get("bolt_count", 0)),
                            score=float(c.get("score", 0.0)),
                            confidence=float(c.get("confidence", 0.0)),
                            status=str(c.get("status", "REVIEW")),
                            evidence=c.get("evidence", {}),
                            leader_distance=c.get("leader_distance"),
                            joint_distance=c.get("joint_distance"),
                            competing_owner=c.get("competing_owner"),
                        )
                    )
            except Exception:
                pass
        return items

    def get_drawings(self) -> list[ShopDrawingItem]:
        """Discover actual generated shop drawings in shop_drawings/."""
        shop_dir = self.output_dir / "shop_drawings"
        items: list[ShopDrawingItem] = []
        if not shop_dir.exists():
            return items

        for json_path in sorted(shop_dir.glob("*.json")):
            if json_path.name == "generation_manifest.json":
                continue
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                bm = str(data.get("backmark", "")).strip()
                dwg_id = str(data.get("drawing_id", f"429B{bm}"))
                dxf_file = shop_dir / f"{dwg_id}.dxf"
                pdf_file = shop_dir / f"{dwg_id}.pdf"

                items.append(
                    ShopDrawingItem(
                        drawing_id=dwg_id,
                        backmark=bm,
                        section=str(data.get("section", "")),
                        length_mm=float(data.get("length_mm", 0.0)),
                        quantity=int(data.get("quantity") or data.get("qty") or 1),
                        status=str(data.get("status", "FABRICATION READY")),
                        scale=data.get("scale", {}).get("label", "1:10") if isinstance(data.get("scale"), dict) else "1:10",
                        hole_count=len(data.get("holes", [])),
                        has_pdf=pdf_file.exists(),
                        has_dxf=dxf_file.exists(),
                        has_json=True,
                    )
                )
            except Exception:
                pass

        return items

    def get_drawing_detail(self, backmark: str) -> Optional[dict[str, Any]]:
        """Return the canonical ShopDrawingModel JSON sidecar for a single member."""
        shop_dir = self.output_dir / "shop_drawings"
        candidates = [
            shop_dir / f"429B{backmark}.json",
            shop_dir / f"{backmark}.json",
        ]
        for p in candidates:
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return None

    def get_bom(self) -> list[BOMItem]:
        """Parse job_bom.json or job_bom.csv."""
        bom_file = self.output_dir / "job_bom.json"
        items: list[BOMItem] = []
        if bom_file.exists():
            try:
                data = json.loads(bom_file.read_text(encoding="utf-8"))
                rows = data if isinstance(data, list) else data.get("members", data.get("rows", []))
                for row in rows:
                    items.append(
                        BOMItem(
                            backmark=str(row.get("backmark", "")),
                            section=str(row.get("section", "")),
                            quantity=int(row.get("quantity") or row.get("qty") or row.get("member_qty") or 1),
                            length_mm=float(row.get("length_mm", 0.0)),
                            weight_kg=float(row.get("weight_kg", row.get("total_weight_kg", 0.0))),
                            bolt_type=str(row.get("bolt_type", row.get("item", "-"))),
                            bolt_count=int(row.get("bolt_count", row.get("total_qty", 0))),
                            hole_diameter_mm=float(row.get("hole_diameter_mm", 0.0)),
                            status=str(row.get("quantity_status", row.get("status", "VALIDATED"))),
                        )
                    )
            except Exception:
                pass
        return items

    def get_regression(self) -> list[RegressionDrawingScore]:
        """Parse regression_4_drawings.json."""
        reg_file = self.output_dir / "regression_4_drawings.json"
        items: list[RegressionDrawingScore] = []
        if reg_file.exists():
            try:
                data = json.loads(reg_file.read_text(encoding="utf-8"))
                for d in data.get("drawings", []):
                    bm = d.get("backmark", "")
                    items.append(
                        RegressionDrawingScore(
                            drawing_id=str(d.get("drawing_id", f"429B{bm}")),
                            backmark=str(bm),
                            metadata_score=float(d.get("metadata_score", 0.0)),
                            fabrication_score=float(d.get("fabrication_score", 0.0)),
                            geometry_score=float(d.get("geometry_score", 0.0)),
                            dimension_score=float(d.get("dimension_score", 0.0)),
                            layout_score=float(d.get("layout_score", 0.0)),
                            visual_score=float(d.get("visual_score", 0.0)),
                            overall_score=float(d.get("overall_score", 0.0)),
                            status=str(d.get("status", "FAIL")),
                            missing_fields=d.get("missing_fields", []),
                            provenance_issues=d.get("provenance_issues", []),
                            differences=d.get("differences", []),
                        )
                    )
            except Exception:
                pass
        return items

    def get_review_queue(self) -> list[ReviewItem]:
        """Parse review_queue.json."""
        rev_file = self.output_dir / "review_queue.json"
        items: list[ReviewItem] = []
        if rev_file.exists():
            try:
                data = json.loads(rev_file.read_text(encoding="utf-8"))
                for item in data.get("items", data.get("queue", [])):
                    items.append(
                        ReviewItem(
                            backmark=str(item.get("backmark", "")),
                            drawing_id=str(item.get("drawing_id", "")),
                            status=str(item.get("status", "BLOCKED")),
                            severity=str(item.get("severity", "BLOCKING")),
                            reason=str(item.get("reason", "")),
                            evidence=str(item.get("evidence", "")),
                            source=str(item.get("source", "MEMBER_SCHEDULE")),
                            confidence=float(item.get("confidence", 1.0)),
                            recommended_action=str(item.get("recommended_action", "")),
                        )
                    )
            except Exception:
                pass
        return items
