"""Job manager: handles file-based persistence and lifecycle for analysis jobs."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.config import JOBS_ROOT
from backend.schemas import Job, JobStatus, PipelineStage


class JobManager:
    def __init__(self, root: Path = JOBS_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create_job(
        self,
        name: str,
        dxf_filename: str,
        dxf_bytes: bytes,
        design_json_bytes: Optional[bytes] = None,
        parameters: Optional[dict[str, Any]] = None,
    ) -> Job:
        """Create a new job directory, persist input files, and write initial job.json."""
        job_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        job_dir = self.root / job_id
        input_dir = job_dir / "input"
        output_dir = job_dir / "output"
        logs_dir = job_dir / "logs"

        input_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Save uploaded DXF
        dxf_path = input_dir / dxf_filename
        dxf_path.write_bytes(dxf_bytes)

        # Save optional design input JSON if provided
        design_path = None
        if design_json_bytes:
            design_path = input_dir / "engineering_input.json"
            design_path.write_bytes(design_json_bytes)

        job_params = parameters or {}
        job_params["dxf_path"] = str(dxf_path)
        if design_path:
            job_params["engineering_input_path"] = str(design_path)

        now = datetime.now(timezone.utc).isoformat()
        job = Job(
            id=job_id,
            name=name or dxf_filename,
            source_dxf=dxf_filename,
            created_at=now,
            status=JobStatus.CREATED,
            current_stage=PipelineStage.UPLOAD,
            progress=0,
            output_dir=str(output_dir),
            parameters=job_params,
        )

        self._save_job(job)
        return job

    def get_job(self, job_id: str) -> Optional[Job]:
        """Load job by ID."""
        job_file = self.root / job_id / "job.json"
        if not job_file.exists():
            return None
        try:
            data = json.loads(job_file.read_text(encoding="utf-8"))
            return Job(**data)
        except Exception:
            return None

    def list_jobs(self) -> list[Job]:
        """List all jobs in chronological descending order."""
        jobs: list[Job] = []
        if not self.root.exists():
            return jobs

        for job_dir in sorted(self.root.iterdir(), reverse=True):
            if job_dir.is_dir():
                job_file = job_dir / "job.json"
                if job_file.exists():
                    try:
                        data = json.loads(job_file.read_text(encoding="utf-8"))
                        jobs.append(Job(**data))
                    except Exception:
                        pass
        return jobs

    def update_job(self, job_id: str, **updates: Any) -> Optional[Job]:
        """Update job fields and persist changes."""
        job = self.get_job(job_id)
        if not job:
            return None

        for k, v in updates.items():
            if hasattr(job, k):
                setattr(job, k, v)

        self._save_job(job)
        return job

    def delete_job(self, job_id: str) -> bool:
        """Delete a job directory and its data from disk."""
        import shutil
        job_dir = self.root / job_id
        if job_dir.exists() and job_dir.is_dir():
            shutil.rmtree(job_dir, ignore_errors=True)
            return True
        return False

    def _save_job(self, job: Job) -> None:
        job_dir = self.root / job.id
        job_dir.mkdir(parents=True, exist_ok=True)
        job_file = job_dir / "job.json"
        job_file.write_text(job.model_dump_json(indent=2), encoding="utf-8")


job_manager = JobManager()

