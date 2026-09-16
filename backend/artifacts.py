"""Safe artifact file resolution with strict path traversal prevention."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from fastapi import HTTPException
from backend.jobs import job_manager


def resolve_job_artifact(job_id: str, relative_path: str) -> Path:
    """Resolve and sanitize artifact path within a job's output or logs directory."""
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    output_dir = Path(job.output_dir).resolve()
    job_dir = output_dir.parent.resolve()

    # Reject null bytes or suspicious characters
    if "\0" in relative_path:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Clean the path
    clean_rel = os.path.normpath(relative_path).lstrip("/\\")

    # Check output directory first, then job root (for logs/engine.log or job.json)
    target_path = (output_dir / clean_rel).resolve()
    if not str(target_path).startswith(str(job_dir)):
        raise HTTPException(status_code=403, detail="Path traversal forbidden")

    if not target_path.exists() or not target_path.is_file():
        # Check in job_dir directly (e.g. logs/engine.log)
        alt_path = (job_dir / clean_rel).resolve()
        if alt_path.exists() and alt_path.is_file() and str(alt_path).startswith(str(job_dir)):
            return alt_path
        raise HTTPException(status_code=404, detail=f"Artifact '{clean_rel}' not found")

    return target_path
