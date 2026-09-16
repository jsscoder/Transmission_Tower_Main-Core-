"""Process runner for executing the real Python engineering pipeline with live stdout/stderr parsing.

Uses standard subprocess.Popen in a background worker thread to ensure 100% reliable
execution on Windows regardless of event loop implementation (Selector vs Proactor).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config import (
    DEFAULT_DESIGN_INPUT,
    DEFAULT_RULES,
    ENGINE_PYTHON,
    ENGINE_ROOT,
    FIXTURES_DIR,
    GOLDEN_GATE_SCRIPT,
    INFERENCE_SCRIPT,
)
from backend.jobs import job_manager
from backend.progress import progress_broadcaster
from backend.schemas import JobProgressEvent, JobStatus, PipelineStage


STAGE_MAP = [
    ("[1/7]", PipelineStage.MEMBER_EXTRACTION, 15, "Extracting member schedule and backmark locators"),
    ("[2/7]", PipelineStage.CALLOUT_EXTRACTION, 30, "Extracting and clustering native B1 bolt callouts"),
    ("[3/7]", PipelineStage.INFERENCE, 45, "Detecting member connection candidates"),
    ("[4/7]", PipelineStage.GEOMETRY, 60, "Constrained CAD geometry and joint topology"),
    ("[5/7]", PipelineStage.TOPOLOGY, 75, "Conservative connection allocation and joint resolution"),
    ("[6/7]", PipelineStage.SHOP_DRAWINGS, 88, "Generating CAD profile DXFs, vector PDFs, and JSON sidecars"),
    ("[7/7]", PipelineStage.BOM, 95, "Aggregating canonical fabrication Bill of Materials"),
    ("[DONE]", PipelineStage.COMPLETE, 100, "Analysis and drawing generation complete"),
]


def _safe_broadcast(loop: asyncio.AbstractEventLoop, event: JobProgressEvent) -> None:
    """Thread-safe broadcast helper to post SSE events to the main asyncio loop."""
    try:
        asyncio.run_coroutine_threadsafe(progress_broadcaster.broadcast(event), loop)
    except Exception:
        pass


def _run_job_worker(job_id: str, loop: asyncio.AbstractEventLoop) -> None:
    """Worker function executed in a dedicated background thread."""
    job = job_manager.get_job(job_id)
    if not job:
        return

    now = datetime.now(timezone.utc).isoformat()
    job_manager.update_job(
        job_id,
        status=JobStatus.RUNNING,
        started_at=now,
        current_stage=PipelineStage.DXF_PARSE,
        progress=5,
        error=None,
    )

    dxf_path = job.parameters.get("dxf_path")
    eng_input = job.parameters.get("engineering_input_path") or str(DEFAULT_DESIGN_INPUT)
    rules_path = str(DEFAULT_RULES)
    output_dir = Path(job.output_dir)
    log_file_path = output_dir.parent / "logs" / "engine.log"

    cmd = [
        str(ENGINE_PYTHON),
        str(INFERENCE_SCRIPT),
        "--dxf", str(dxf_path),
        "--out", str(output_dir),
        "--engineering-input", str(eng_input),
        "--rules", str(rules_path),
        "--generate",
    ]

    start_time = time.time()
    completed_stages: list[str] = ["UPLOAD"]

    _safe_broadcast(
        loop,
        JobProgressEvent(
            job_id=job_id,
            stage=PipelineStage.DXF_PARSE.value,
            progress=5,
            message="Initializing DXF parser and reading entities",
            elapsed_seconds=0.0,
            completed_stages=completed_stages,
        ),
    )

    try:
        log_file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file_path, "w", encoding="utf-8", buffering=1) as log_file:
            log_file.write(f"=== TRANSMISSION TOWER ENGINE LAUNCH: {now} ===\n")
            log_file.write(f"Command: {' '.join(cmd)}\n")
            log_file.write(f"Working Dir: {ENGINE_ROOT}\n\n")
            log_file.flush()

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(ENGINE_ROOT),
                bufsize=1,
            )

            current_progress = 5
            current_stage_name = PipelineStage.DXF_PARSE.value

            for raw_line in proc.stdout:
                line = raw_line.rstrip("\r\n")
                log_file.write(line + "\n")
                log_file.flush()

                # Match stage markers
                for marker, stage_enum, progress_pct, desc in STAGE_MAP:
                    if marker in line:
                        current_progress = progress_pct
                        current_stage_name = stage_enum.value
                        if desc not in completed_stages and progress_pct < 100:
                            completed_stages.append(desc)

                        elapsed = round(time.time() - start_time, 1)
                        job_manager.update_job(
                            job_id,
                            current_stage=stage_enum,
                            progress=progress_pct,
                        )
                        _safe_broadcast(
                            loop,
                            JobProgressEvent(
                                job_id=job_id,
                                stage=current_stage_name,
                                progress=current_progress,
                                message=line if len(line) < 90 else desc,
                                elapsed_seconds=elapsed,
                                completed_stages=completed_stages,
                            ),
                        )
                        break

            ret_code = proc.wait()
            log_file.write(f"\n=== PROCESS TERMINATED WITH RETURN CODE: {ret_code} ===\n")
            log_file.flush()

        if ret_code != 0:
            err_msg = f"Engine subprocess failed with exit code {ret_code}"
            job_manager.update_job(
                job_id,
                status=JobStatus.FAILED,
                error=err_msg,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            _safe_broadcast(
                loop,
                JobProgressEvent(
                    job_id=job_id,
                    stage="FAILED",
                    progress=current_progress,
                    message=err_msg,
                    elapsed_seconds=round(time.time() - start_time, 1),
                    completed_stages=completed_stages,
                ),
            )
            return

        # Execute post-processing: Golden Gate scoring and PNG visual cards
        shop_dir = output_dir / "shop_drawings"
        if shop_dir.exists() and any(shop_dir.glob("*.dxf")):
            # 1. Golden Gate scoring
            try:
                subprocess.run(
                    [
                        str(ENGINE_PYTHON),
                        str(GOLDEN_GATE_SCRIPT),
                        "--fixtures", str(FIXTURES_DIR),
                        "--shop", str(shop_dir),
                        "--out", str(output_dir),
                    ],
                    cwd=str(ENGINE_ROOT),
                    timeout=30,
                    capture_output=True,
                )
            except Exception as e:
                with open(log_file_path, "a", encoding="utf-8") as f:
                    f.write(f"Warning: Golden Gate scoring failed: {e}\n")

            # 2. Raster PNG export & comparison cards
            try:
                vis_dir = output_dir / "visual_comparison"
                vis_dir.mkdir(parents=True, exist_ok=True)
                from export_pngs import export_dxf_to_png
                for dxf_f in shop_dir.glob("*.dxf"):
                    bm = dxf_f.stem.replace("429B", "")
                    out_png = vis_dir / f"generated_429B{bm}.png"
                    export_dxf_to_png(dxf_f, out_png)

                from generate_visual_comparison import create_comparison_assets
                create_comparison_assets(vis_dir, FIXTURES_DIR)
            except Exception as e:
                with open(log_file_path, "a", encoding="utf-8") as f:
                    f.write(f"Warning: Visual comparison generation failed: {e}\n")

        # Mark job completed
        completed_now = datetime.now(timezone.utc).isoformat()
        job_manager.update_job(
            job_id,
            status=JobStatus.COMPLETED,
            current_stage=PipelineStage.COMPLETE,
            progress=100,
            completed_at=completed_now,
        )

        _safe_broadcast(
            loop,
            JobProgressEvent(
                job_id=job_id,
                stage=PipelineStage.COMPLETE.value,
                progress=100,
                message="Pipeline execution completed successfully",
                elapsed_seconds=round(time.time() - start_time, 1),
                completed_stages=completed_stages + ["All stages complete"],
            ),
        )

    except Exception as ex:
        tb_str = traceback.format_exc()
        err_msg = f"{type(ex).__name__}: {ex}"
        try:
            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(f"\n=== CRITICAL RUNNER EXCEPTION ===\n{err_msg}\n{tb_str}\n")
        except Exception:
            pass

        job_manager.update_job(
            job_id,
            status=JobStatus.FAILED,
            error=err_msg,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        _safe_broadcast(
            loop,
            JobProgressEvent(
                job_id=job_id,
                stage="FAILED",
                progress=0,
                message=f"Execution error: {err_msg}",
                elapsed_seconds=round(time.time() - start_time, 1),
                completed_stages=completed_stages,
            ),
        )


async def run_pipeline_job(job_id: str) -> None:
    """Launch the synchronous process runner in a background worker thread."""
    loop = asyncio.get_running_loop()
    threading.Thread(
        target=_run_job_worker,
        args=(job_id, loop),
        daemon=True,
    ).start()
