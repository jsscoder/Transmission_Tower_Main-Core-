"""Configuration for FastAPI backend service and Python engineering engine."""
from __future__ import annotations

import os
from pathlib import Path

# Engine root directory (deliverable)
ENGINE_ROOT = Path(__file__).resolve().parent.parent

# Jobs storage directory
JOBS_ROOT = Path(os.environ.get("JOBS_ROOT", str(ENGINE_ROOT / "jobs")))
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

# Path to python virtual environment executable
ENGINE_PYTHON = Path(
    os.environ.get(
        "PYTHON_ENGINE_PATH",
        str(ENGINE_ROOT / "my_venv" / "Scripts" / "python.exe")
    )
)

# Engine CLI script paths
INFERENCE_SCRIPT = ENGINE_ROOT / "inference.py"
GOLDEN_GATE_SCRIPT = ENGINE_ROOT / "run_golden_gate.py"
PNG_EXPORT_SCRIPT = ENGINE_ROOT / "export_pngs.py"
VIS_COMP_SCRIPT = ENGINE_ROOT / "generate_visual_comparison.py"

# Default rules & design fixtures
DEFAULT_RULES = ENGINE_ROOT / "design_rules.json"
DEFAULT_DESIGN_INPUT = ENGINE_ROOT / "connection_design.json"
FIXTURES_DIR = ENGINE_ROOT / "fixtures"

# Web application server settings
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))
CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "*"
]
