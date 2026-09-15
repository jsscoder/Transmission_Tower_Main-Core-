"""Structured review queue generator for transmission tower detailing.

Identifies, classifies, and formats all unallocated connections, ambiguous joints,
and missing fabrication details into machine-readable JSON and tabular CSV reports.
Severity levels:
- BLOCKING: Drawing/fabrication cannot safely proceed until resolved.
- WARNING: Engineering review or confirmation recommended.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def build_review_queue(
    inventory: dict | None = None,
    resolved: dict | None = None,
    topology: dict | None = None,
    schedule: dict | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Construct actionable review queue from pipeline inventory, resolution, and topology data."""
    items: list[dict[str, Any]] = []
    item_counter = 1

    # 1. Blocked fabrication members from inventory
    if inventory:
        blocked_entries = inventory.get("blocked", [])
        for entry in blocked_entries:
            mark = str(entry.get("backmark", "")).strip()
            drawing_id = f"429B{mark}" if mark else "UNKNOWN"
            raw_reasons = entry.get("reasons")
            if isinstance(raw_reasons, list):
                reasons = [str(r).strip() for r in raw_reasons if str(r).strip()]
            elif isinstance(raw_reasons, str):
                reasons = [r.strip() for r in raw_reasons.split(",") if r.strip()]
            elif entry.get("reason"):
                reasons = [r.strip() for r in str(entry.get("reason")).split(",") if r.strip()]
            else:
                reasons = ["BLOCKED"]

            # Lookup section and length context if schedule available
            sec_info = ""
            len_info = ""
            if schedule and mark in schedule:
                s_rec = schedule[mark]
                sec_info = str(s_rec.get("section", ""))
                len_info = str(s_rec.get("length_mm", ""))

            for reason in reasons:
                item_id = f"RQ-{item_counter:03d}"
                item_counter += 1

                if reason == "MISSING_FABRICATION_HOLES":
                    severity = "BLOCKING"
                    item_type = "MEMBER_FABRICATION"
                    desc = (
                        f"Member {mark} is missing hole detailing; ends have no connection holes "
                        f"or patterns defined. Fabrication drawing cannot be detailed."
                    )
                    ctx = f"Section: {sec_info or 'N/A'}, Length: {len_info or 'N/A'} mm."
                    rec = (
                        "Provide approved connection detailing (hole coordinates, diameters, "
                        "edge distances) in engineering input JSON before fabrication release."
                    )
                elif reason == "MISSING_QTY":
                    severity = "BLOCKING"
                    item_type = "MEMBER_QUANTITY"
                    desc = (
                        f"Member {mark} has null or invalid quantity. "
                        f"Job bill of materials and total piece count cannot be finalized."
                    )
                    ctx = f"Section: {sec_info or 'N/A'}, Length: {len_info or 'N/A'} mm."
                    rec = (
                        "Specify authoritative positive integer quantity from tower erection diagram "
                        "or project schedule."
                    )
                elif reason == "MISSING_APPROVED_ENGINEERING_INPUT":
                    severity = "BLOCKING"
                    item_type = "MEMBER_DESIGN"
                    desc = (
                        f"Member {mark} is identified in assembly schedule but absent from "
                        f"approved engineering design input."
                    )
                    ctx = f"Section: {sec_info or 'N/A'}, Length: {len_info or 'N/A'} mm."
                    rec = "Add member record to approved engineering input JSON with verified dimensions."
                else:
                    severity = "BLOCKING"
                    item_type = "MEMBER_GENERAL"
                    desc = f"Member {mark} blocked from fabrication: {reason}."
                    ctx = f"Section: {sec_info or 'N/A'}, Length: {len_info or 'N/A'} mm."
                    rec = "Review member fabrication status and supply missing engineering data."

                items.append({
                    "item_id": item_id,
                    "backmark": mark,
                    "drawing_id": drawing_id,
                    "item_type": item_type,
                    "severity": severity,
                    "reason": reason,
                    "description": desc,
                    "reviewer_context": ctx,
                    "actionable_recommendation": rec,
                })

    # 2. Connection resolution ambiguities and review items
    if resolved:
        decisions = resolved.get("decisions", [])
        for dec in decisions:
            status = dec.get("status")
            if status in ("REVIEW", "REJECT"):
                mark = str(dec.get("backmark", "")).strip()
                drawing_id = f"429B{mark}" if mark else "UNKNOWN"
                end_id = dec.get("end_id", "N/A")
                joint_id = dec.get("joint_id", "N/A")
                score_raw = dec.get("score")
                try:
                    score = float(score_raw) if score_raw is not None else 0.0
                except (ValueError, TypeError):
                    score = 0.0
                competing = bool(dec.get("competing_owner", False))
                callouts = dec.get("raw_callouts", [])

                item_id = f"RQ-{item_counter:03d}"
                item_counter += 1

                if competing:
                    reason = "AMBIGUOUS_CONNECTION"
                    desc = (
                        f"Connection at member {mark} end {end_id} (joint {joint_id}) shares callout group "
                        f"{dec.get('group_id')} with competing joint member. Bolt allocation is ambiguous."
                    )
                    rec = (
                        "Verify joint framing in assembly CAD and allocate bolt count between "
                        "connected members in approved connection design."
                    )
                elif status == "REVIEW":
                    reason = "REVIEW_REQUIRED"
                    desc = (
                        f"Connection candidate at member {mark} end {end_id} (joint {joint_id}) has moderate "
                        f"confidence score ({score:.2f}) and requires human verification."
                    )
                    rec = (
                        "Inspect CAD geometry at joint location; confirm whether callouts apply to this member."
                    )
                else:  # REJECT
                    reason = "AMBIGUOUS_CONNECTION"
                    desc = (
                        f"Connection candidate at member {mark} end {end_id} rejected due to low "
                        f"confidence ({score:.2f})."
                    )
                    rec = (
                        "Check CAD drawings for unassociated bolt callouts or manual detailing requirements."
                    )

                items.append({
                    "item_id": item_id,
                    "backmark": mark,
                    "drawing_id": drawing_id,
                    "item_type": "CONNECTION",
                    "severity": "WARNING",
                    "reason": reason,
                    "description": desc,
                    "reviewer_context": f"Joint: {joint_id}, End: {end_id}, Score: {score:.2f}, Raw Callouts: {callouts}",
                    "actionable_recommendation": rec,
                })

    # Summary statistics
    reason_counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {"BLOCKING": 0, "WARNING": 0}
    for item in items:
        r = item["reason"]
        reason_counts[r] = reason_counts.get(r, 0) + 1
        s = item["severity"]
        severity_counts[s] = severity_counts.get(s, 0) + 1

    queue_data = {
        "summary": {
            "total_items": len(items),
            "blocking_count": severity_counts["BLOCKING"],
            "warning_count": severity_counts["WARNING"],
            "by_reason": reason_counts,
            "by_severity": severity_counts,
        },
        "items": items,
    }

    if out_dir:
        write_review_queue(queue_data, out_dir)

    return queue_data


def write_review_queue(queue_data: dict[str, Any], out_dir: str | Path) -> None:
    """Write review queue to review_queue.json and review_queue.csv."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "review_queue.json"
    json_path.write_text(json.dumps(queue_data, indent=2), encoding="utf-8")

    csv_path = out / "review_queue.csv"
    fields = [
        "item_id",
        "backmark",
        "drawing_id",
        "item_type",
        "severity",
        "reason",
        "description",
        "reviewer_context",
        "actionable_recommendation",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for item in queue_data.get("items", []):
            writer.writerow({k: item.get(k, "") for k in fields})
