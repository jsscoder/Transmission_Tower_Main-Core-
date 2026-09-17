"""FastAPI application entry point: routes, SSE streaming, and artifact file delivery."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from backend.artifacts import resolve_job_artifact
from backend.config import API_HOST, API_PORT, CORS_ORIGINS
from backend.engine_adapter import EngineAdapter
from backend.jobs import job_manager
from backend.progress import progress_broadcaster
from backend.runner import run_pipeline_job
from backend.schemas import (
    BOMItem,
    CandidateItem,
    Job,
    LocatorItem,
    LocatorsSummary,
    MemberItem,
    PipelineMetrics,
    RegressionDrawingScore,
    ReviewItem,
    ShopDrawingItem,
    TopologyJoint,
)

app = FastAPI(
    title="Transmission Tower Engineering Automation API",
    description="Real-time orchestration and visualization API for tower DXF extraction and CAD shop drawing detailing.",
    version="1.0.0",
)

# Enable CORS for Next.js web application
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "tower-engineering-core"}


# ============================================================
# JOBS CRUD & RUNNER
# ============================================================

@app.post("/api/jobs", response_model=Job)
async def create_job(
    name: Optional[str] = Form(None),
    dxf: UploadFile = File(...),
    engineering_input: Optional[UploadFile] = File(None),
    auto_run: bool = Form(True),
    background_tasks: BackgroundTasks = None,
):
    """Upload a real DXF and create a new pipeline job."""
    if not dxf.filename or not dxf.filename.lower().endswith(".dxf"):
        raise HTTPException(status_code=400, detail="Uploaded file must be a .dxf file")

    dxf_bytes = await dxf.read()
    design_bytes = await engineering_input.read() if engineering_input else None

    job_name = name or dxf.filename
    job = job_manager.create_job(
        name=job_name,
        dxf_filename=dxf.filename,
        dxf_bytes=dxf_bytes,
        design_json_bytes=design_bytes,
    )

    if auto_run:
        # Launch engine runner in background
        asyncio.create_task(run_pipeline_job(job.id))

    return job


@app.get("/api/jobs", response_model=list[Job])
def list_jobs():
    """List all jobs ordered by creation date."""
    return job_manager.list_jobs()


@app.get("/api/jobs/{job_id}", response_model=Job)
def get_job(job_id: str):
    """Get status and metadata for a specific job."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/jobs/{job_id}/run", response_model=Job)
def run_job(job_id: str):
    """Trigger pipeline execution for a previously created job."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    asyncio.create_task(run_pipeline_job(job_id))
    return job_manager.get_job(job_id)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    """Delete a job and remove its workspace from disk."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    success = job_manager.delete_job(job_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete job directory")
    return {"status": "deleted", "job_id": job_id}


@app.get("/api/jobs/{job_id}/logs")
def get_job_logs(job_id: str):
    """Get raw engine execution log as JSON text."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    log_path = Path(job.output_dir).parent / "logs" / "engine.log"
    logs = ""
    if log_path.exists():
        try:
            logs = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logs = f"Error reading log file: {e}"

    return {
        "job_id": job_id,
        "logs": logs,
        "status": job.status,
        "current_stage": job.current_stage,
        "progress": job.progress,
    }


@app.get("/api/jobs/{job_id}/logs/raw")
def get_job_logs_raw(job_id: str):
    """Stream raw engine log directly as plain text in the browser."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    log_path = Path(job.output_dir).parent / "logs" / "engine.log"
    logs = ""
    if log_path.exists():
        try:
            logs = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            logs = ""

    return PlainTextResponse(content=logs, media_type="text/plain; charset=utf-8")


@app.get("/api/jobs/{job_id}/events")
async def get_job_events(job_id: str):
    """Server-Sent Events (SSE) stream for live progress tracking."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return EventSourceResponse(progress_broadcaster.event_generator(job_id))


# ============================================================
# REAL ENGINE DATA ENDPOINTS
# ============================================================

def _adapter_for_job(job_id: str) -> EngineAdapter:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return EngineAdapter(job.output_dir, job_id=job_id)


@app.get("/api/jobs/{job_id}/metrics", response_model=PipelineMetrics)
def get_job_metrics(job_id: str):
    """Get real KPI metrics from actual pipeline outputs."""
    adapter = _adapter_for_job(job_id)
    return adapter.get_metrics()


@app.get("/api/jobs/{job_id}/members", response_model=list[MemberItem])
def get_job_members(job_id: str):
    """Get extracted member schedule with status."""
    adapter = _adapter_for_job(job_id)
    return adapter.get_members()


@app.get("/api/jobs/{job_id}/locators", response_model=list[LocatorItem])
def get_job_locators(job_id: str, kind: str = "all"):
    """Get extracted locators and bolt callouts with CAD coordinates and PNG artifacts."""
    adapter = _adapter_for_job(job_id)
    return adapter.get_locators(kind=kind)


@app.get("/api/jobs/{job_id}/locators/summary", response_model=LocatorsSummary)
def get_job_locators_summary(job_id: str):
    """Get complete locators summary including assembly image and breakdown."""
    adapter = _adapter_for_job(job_id)
    return adapter.get_locators_summary()


@app.get("/api/jobs/{job_id}/topology", response_model=list[TopologyJoint])
def get_job_topology(job_id: str):
    """Get joint-first topology graph nodes."""
    adapter = _adapter_for_job(job_id)
    return adapter.get_topology()


@app.get("/api/jobs/{job_id}/inference", response_model=list[CandidateItem])
def get_job_inference(job_id: str):
    """Get candidate connection allocations and confidence scores."""
    adapter = _adapter_for_job(job_id)
    return adapter.get_inference()


@app.get("/api/jobs/{job_id}/drawings", response_model=list[ShopDrawingItem])
def get_job_drawings(job_id: str):
    """Get list of actual generated shop drawings."""
    adapter = _adapter_for_job(job_id)
    drawings = adapter.get_drawings()
    # Populate download URLs
    for dwg in drawings:
        dwg.pdf_url = f"/api/jobs/{job_id}/artifacts/shop_drawings/{dwg.drawing_id}.pdf"
        dwg.dxf_url = f"/api/jobs/{job_id}/artifacts/shop_drawings/{dwg.drawing_id}.dxf"
        dwg.json_url = f"/api/jobs/{job_id}/artifacts/shop_drawings/{dwg.drawing_id}.json"
    return drawings


@app.get("/api/jobs/{job_id}/drawings/{backmark}")
def get_job_drawing_detail(job_id: str, backmark: str):
    """Get canonical JSON sidecar for a specific drawing."""
    adapter = _adapter_for_job(job_id)
    detail = adapter.get_drawing_detail(backmark)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Drawing for member {backmark} not found")
    return detail


@app.get("/api/jobs/{job_id}/bom", response_model=list[BOMItem])
def get_job_bom(job_id: str):
    """Get canonical Bill of Materials."""
    adapter = _adapter_for_job(job_id)
    return adapter.get_bom()


@app.get("/api/jobs/{job_id}/regression", response_model=list[RegressionDrawingScore])
def get_job_regression(job_id: str):
    """Get Golden Gate regression scorecard for members 37, 44, 45, 46."""
    adapter = _adapter_for_job(job_id)
    scores = adapter.get_regression()
    # Populate visual preview URLs
    for s in scores:
        s.preview_url = f"/api/jobs/{job_id}/artifacts/visual_comparison/generated_429B{s.backmark}.png"
        s.reference_url = f"/api/jobs/{job_id}/artifacts/visual_comparison/reference_429B{s.backmark}.png"
        s.diff_url = f"/api/jobs/{job_id}/artifacts/visual_comparison/diff_429B{s.backmark}.png"
    return scores


@app.get("/api/jobs/{job_id}/review", response_model=list[ReviewItem])
def get_job_review(job_id: str):
    """Get actionable review queue items."""
    adapter = _adapter_for_job(job_id)
    return adapter.get_review_queue()


@app.get("/api/jobs/{job_id}/scale-context")
def get_job_scale_context(job_id: str):
    """Get scale context, extents, and adaptive tolerances for this job."""
    adapter = _adapter_for_job(job_id)
    ctx = adapter.get_scale_context()
    if ctx is None:
        raise HTTPException(status_code=404, detail="Scale context not available for this job")
    return ctx


# ============================================================
# SAFE ARTIFACT FILE DELIVERY
# ============================================================

@app.get("/api/jobs/{job_id}/artifacts/{artifact_path:path}")
def get_artifact_file(job_id: str, artifact_path: str):
    """Securely stream output artifact file (DXF, PDF, JSON, PNG, CSV, log)."""
    file_path = resolve_job_artifact(job_id, artifact_path)

    media_types = {
        ".pdf": "application/pdf",
        ".dxf": "application/dxf",
        ".json": "application/json",
        ".csv": "text/csv",
        ".png": "image/png",
        ".log": "text/plain",
        ".txt": "text/plain",
    }
    ext = file_path.suffix.lower()
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=file_path.name,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host=API_HOST, port=API_PORT, reload=True)
