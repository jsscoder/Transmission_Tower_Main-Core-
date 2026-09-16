# TRANSMISSION TOWER WEB & BACKEND INTEGRATION AUDIT

**Target**: Full-stack Next.js + FastAPI orchestration platform for the Transmission Tower Engineering Automation Pipeline.
**Date**: 2026-09-15
**Audit Stage**: Phase 1 (Architectural & Engine Pre-Flight Audit)

---

## 1. Engine Entry Point & Invocation Protocol

- **Executable**: `my_venv\Scripts\python.exe` (Python 3.12.13, virtual environment in `deliverable/my_venv`)
- **Primary CLI**: `inference.py`
  ```bash
  python inference.py --dxf <path_to_dxf> --out <output_dir> --engineering-input <path_to_json> --rules <rules_json> --generate
  ```
- **Execution Mechanism**:
  The engine runs through 7 sequential pipeline stages:
  1. `[1/7] Member schedule + locators` (extracts schedule table and backmark locators from assembly DXF)
  2. `[2/7] Native B1 bolt callouts + grouping` (clusters bolt callout texts and counts)
  3. `[3/7] Member connection candidates` (associates members with candidate callouts)
  4. `[4/7] Constrained CAD geometry + joint topology` (constructs physical joint graph, connects member ends)
  5. `[5/7] Conservative connection resolution` (allocates bolt groups to joints)
  6. `[6/7] Shop drawing generation` (renders CAD profiles, dimension chains, title blocks, vector PDFs, JSON sidecars)
  7. `[7/7] Job BOM` (aggregates canonical fabrication bill of materials)
  8. `[REPORT]` and `[DONE]`
- **Golden Gate Scoring Tool**:
  ```bash
  python run_golden_gate.py --fixtures fixtures --shop <output_dir>/shop_drawings --out <output_dir>
  ```
  Produces `regression_4_drawings.json` and `regression_4_drawings.csv`.
- **PNG Visual Comparison Tool**:
  ```bash
  python export_pngs.py
  python generate_visual_comparison.py
  ```
  Renders CAD drawings to high-res PNG and side-by-side verification cards in `visual_comparison/`.

---

## 2. Real Output Files & Artifact Topology

The pipeline outputs all artifacts into a caller-specified `--out <dir>` (default: `pipeline_out`). In the web application, each job will have its own isolated directory: `jobs/<job_id>/output/`.

| File Name | Format | Source Subsystem | Contents / Purpose |
| :--- | :---: | :--- | :--- |
| `pipeline_summary.json` | JSON | `inference.py` | Overall job metrics, status counts (AUTO, REVIEW, REJECT, BLOCKED). |
| `run_summary.json` | JSON | `inference.py` / `shop_reporting.py` | Gate validation counts (buildable, blocked, generated, reference pass). |
| `manifest.json` | JSON | `inference.py` | Job run metadata and file index. |
| `member_schedule.json` / `.csv` | JSON/CSV | `tower.py` | Extracted member schedule table (all 37 members: backmark, section, length, count). |
| `bolt_callouts.json` / `.csv` | JSON/CSV | `connection_annotations.py` | Extracted B1 callout texts, bounding boxes, and coordinates. |
| `connection_callout_groups.json` / `.csv` | JSON/CSV | `connection_annotations.py` | Clustered callout groups near tower joints. |
| `member_connection_candidates.json` / `.csv` | JSON/CSV | `connection_association.py` | Candidate connections with distance, angle, and confidence scores. |
| `connection_topology.json` | JSON | `connection_topology.py` | Physical joint graph (50 joints, member ends, mapped groups). |
| `drawing_inventory.json` / `.csv` | JSON/CSV | `shop_reporting.py` | Fabrication status for all members (`BUILDABLE` vs `BLOCKED` with reasons). |
| `shop_drawings/429B*.dxf` | DXF | `shop_drawing.py` | Editable CAD shop drawing files with standard IS 802 layers. |
| `shop_drawings/429B*.pdf` | PDF | `shop_drawing.py` | Headless vector PDF fabrication drawings (A3 layout). |
| `shop_drawings/429B*.json` | JSON | `shop_drawing.py` | Canonical `ShopDrawingModel` JSON sidecar with full object hierarchy & provenance. |
| `job_bom.json` / `.csv` | JSON/CSV | `bom.py` | Aggregated fabrication Bill of Materials (weights, bolts, member quantities). |
| `review_queue.json` / `.csv` | JSON/CSV | `review_queue.py` | Actionable queue of blocked members and ambiguous joints for engineer review. |
| `regression_4_drawings.json` / `.csv` | JSON/CSV | `golden_score.py` | Golden Gate regression scorecard for members 37, 44, 45, 46. |
| `visual_comparison/` | Directory | `export_pngs.py` | Rasterized PNG previews, reference cards, and composite diff images. |
| `pipeline.log` | Text | `app_logger.py` | Timestamped execution log from the engine run. |

---

## 3. Real-Time Log & Progress Parsing

The engine logs stage transitions with standard markers:
- `[1/7] Member schedule + locators` $\to$ Progress: 15%
- `[2/7] Native B1 bolt callouts + grouping` $\to$ Progress: 30%
- `[3/7] Member connection candidates` $\to$ Progress: 45%
- `[4/7] Constrained CAD geometry + joint topology` $\to$ Progress: 60%
- `[5/7] Conservative connection resolution` $\to$ Progress: 75%
- `[6/7] Shop drawing generation` $\to$ Progress: 90%
- `[7/7] Job BOM` $\to$ Progress: 95%
- `[DONE]` $\to$ Progress: 100%

The FastAPI `runner.py` will read process stdout asynchronously and emit SSE events with:
```json
{
  "stage": "GEOMETRY_TOPOLOGY",
  "progress": 60,
  "message": "Constrained CAD geometry + joint topology",
  "elapsed_seconds": 42.1
}
```

---

## 4. Current Environment & Dependencies

- **Node.js**: Installed at `C:\Program Files\nodejs\node.exe` (v26.8.2), `npm` (11.19.1).
- **Python Virtualenv**: `deliverable/my_venv` (Python 3.12.13).
  - Already installed: `ezdxf`, `matplotlib`, `numpy`, `pillow`, `scipy`.
  - Required additions for FastAPI: `fastapi`, `uvicorn`, `sse-starlette`, `pydantic`, `python-multipart`.
- **Frontend Stack**: Next.js 14+ (App Router), TypeScript, Tailwind CSS, Lucide React icons.

---

## 5. Identified Integration Risks & Mitigations

1. **Subprocess Execution Duration**:
   - *Risk*: A full 37-member assembly extraction takes ~1.5 to 2.5 minutes on this machine.
   - *Mitigation*: The backend must run the engine asynchronously in a background thread/process and return the `job_id` immediately. The UI connects via Server-Sent Events (SSE) to display live progress.
2. **File Path & Traversal Security**:
   - *Risk*: Path traversal attacks via `/api/jobs/:id/artifacts/:path`.
   - *Mitigation*: Strictly resolve paths against `jobs/<job_id>/output/` using `os.path.commonpath` or `Path.resolve().is_relative_to()`. Reject any `..` or absolute paths.
3. **Preserving the Golden Gate Milestone**:
   - *Risk*: Modifying Python scripts to suit the web app might break the 70 unit tests or the Golden Gate ($\ge 90\%$).
   - *Mitigation*: Do not change the core engineering modules (`shop_drawing.py`, `inference.py`, `domain/`, etc.). The web app is purely an orchestration, extraction, and presentation layer.
