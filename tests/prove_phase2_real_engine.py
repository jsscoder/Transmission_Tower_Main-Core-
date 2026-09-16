"""Demonstrate and prove real DXF upload, execution, and API retrieval (Phase 2 Gate)."""
from __future__ import annotations

import time
from pathlib import Path
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from backend.main import app

client = TestClient(app)
dxf_path = ROOT / "loader" / "WO_429_LLA1_110kv_NT_PART P1 TO P3_ST No. 1 OF 6.dxf"
design_path = ROOT / "connection_design.json"

print("=" * 70)
print("PHASE 2 PROOF: REAL DXF UPLOAD -> PYTHON ENGINE RUNS -> REAL API DATA")
print("=" * 70)

# 1. Post real DXF
print("\n[1/4] Uploading real DXF and creating job via POST /api/jobs ...")
with open(dxf_path, "rb") as f_dxf, open(design_path, "rb") as f_des:
    res = client.post(
        "/api/jobs",
        data={"name": "WO_429_Live_Verification_Job", "auto_run": "false"},
        files={
            "dxf": (dxf_path.name, f_dxf, "application/dxf"),
            "engineering_input": (design_path.name, f_des, "application/json"),
        },
    )

assert res.status_code == 200, f"Upload failed: {res.text}"
job = res.json()
job_id = job["id"]
print(f"  Created Job ID: {job_id}")
print(f"  Source DXF    : {job['source_dxf']}")
print(f"  Status        : {job['status']}")

# 2. Run engine
print("\n[2/4] Triggering Python engine run via runner...")
import asyncio
from backend.runner import run_pipeline_job

t0 = time.time()
asyncio.run(run_pipeline_job(job_id))
elapsed = round(time.time() - t0, 1)
print(f"  Engine finished execution in {elapsed}s")

# 3. Verify Job status via API
res_job = client.get(f"/api/jobs/{job_id}")
assert res_job.status_code == 200
job_status = res_job.json()
print(f"  Job Status: {job_status['status']} | Progress: {job_status['progress']}%")
assert job_status["status"] == "COMPLETED", f"Expected COMPLETED, got {job_status['status']}"

# 4. Verify Real Outputs via API Endpoints
print("\n[3/4] Querying real outputs via API endpoints...")

# Metrics
res_metrics = client.get(f"/api/jobs/{job_id}/metrics")
assert res_metrics.status_code == 200
metrics = res_metrics.json()
print(f"  /api/jobs/{job_id}/metrics:")
print(f"    - Total Members: {metrics['members_total']}")
print(f"    - Buildable    : {metrics['buildable_count']}")
print(f"    - Blocked      : {metrics['blocked_count']}")
print(f"    - DXF Drawings : {metrics['generated_drawings']}")
print(f"    - Golden Gate  : {metrics['golden_gate_status']}")

# Members
res_members = client.get(f"/api/jobs/{job_id}/members")
assert res_members.status_code == 200
members = res_members.json()
print(f"  /api/jobs/{job_id}/members: {len(members)} real members retrieved")

# Shop drawings
res_drawings = client.get(f"/api/jobs/{job_id}/drawings")
assert res_drawings.status_code == 200
drawings = res_drawings.json()
print(f"  /api/jobs/{job_id}/drawings: {len(drawings)} real drawings generated")
for dwg in drawings:
    print(f"    * {dwg['drawing_id']} ({dwg['section']}, L={dwg['length_mm']}mm, qty={dwg['quantity']})")

# Regression
res_reg = client.get(f"/api/jobs/{job_id}/regression")
assert res_reg.status_code == 200
scores = res_reg.json()
print(f"  /api/jobs/{job_id}/regression: {len(scores)} golden regression drawings scored")
for s in scores:
    print(f"    * {s['drawing_id']}: Score={s['overall_score']}% [{s['status']}]")

# BOM
res_bom = client.get(f"/api/jobs/{job_id}/bom")
assert res_bom.status_code == 200
bom = res_bom.json()
print(f"  /api/jobs/{job_id}/bom: {len(bom)} BOM rows aggregated")

# Artifact download test
res_art = client.get(f"/api/jobs/{job_id}/artifacts/pipeline_summary.json")
assert res_art.status_code == 200
print(f"\n[4/4] Artifact download verified: {len(res_art.content)} bytes")

print("\n" + "=" * 70)
print("PHASE 2 PROVEN: REAL ENGINE EXECUTES AND PRODUCES 100% REAL DATA VIA API")
print("=" * 70)
