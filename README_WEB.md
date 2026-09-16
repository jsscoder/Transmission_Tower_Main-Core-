# Transmission Tower Engineering Automation Web Application

Production Next.js + FastAPI orchestration platform for the Transmission Tower Engineering Automation Core.

---

## 1. Quick Start Guide

### Prerequisites
- **Node.js**: v18+ (tested with v26.8.2) & npm
- **Python**: 3.12+ (tested with 3.12.13 in `deliverable/my_venv`)

---

### Step 1: Start the FastAPI Python Backend

Open **Terminal 1** in `deliverable/`:

```powershell
# From deliverable directory
.\my_venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

- **Backend API**: [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **API Health Check**: [http://localhost:8000/api/health](http://localhost:8000/api/health)

---

### Step 2: Start the Next.js Frontend

Open **Terminal 2** in `deliverable/web/`:

```powershell
cd web
npm run dev
```

- **Web Dashboard**: [http://localhost:3000](http://localhost:3000)

---

## 2. End-to-End User & Demo Flow

1. Navigate to `http://localhost:3000` (automatically redirects to `/jobs`).
2. Click **"New DXF Job"**.
3. Upload an authoritative tower assembly DXF (e.g. `loader/WO_429_LLA1_110kv_NT_PART P1 TO P3_ST No. 1 OF 6.dxf`).
4. (Optional) Provide approved engineering connection input JSON.
5. Click **"Analyze Transmission Tower"**.
6. Watch **Real Live Progress** via Server-Sent Events (SSE):
   - `[1/7] Member schedule + locators`
   - `[2/7] Native B1 bolt callouts + grouping`
   - `[3/7] Member connection candidates`
   - `[4/7] Constrained CAD geometry + joint topology`
   - `[5/7] Conservative connection resolution`
   - `[6/7] Shop drawing generation (DXF, PDF, JSON)`
   - `[7/7] Canonical BOM`
7. Explore live results:
   - **Overview**: Real KPI cards from `pipeline_summary.json` & `run_summary.json`.
   - **Members**: Schedule table of all 37 tower members with status filters and detail drawer.
   - **Locators**: Raw native B1 text entities and clustered coordinates.
   - **Topology**: Visual joint graph (50 joints, member ends, callout groups).
   - **Inference**: Conservative connection candidate allocations and confidence scores.
   - **CAD Shop Drawings**: Interactive drawing viewer with visual CAD preview on left, canonical engineering metadata on right, and direct download buttons for DXF, PDF, and JSON sidecars.
   - **BOM**: Canonical Bill of Materials table with CSV and JSON downloads.
   - **Golden Regression**: Deterministic scorecard for members 37, 44, 45, 46 and composite side-by-side verification diff cards.
   - **Review Queue**: Actionable list of blocked members and ambiguous joints.

---

## 3. Architecture & Security

- **Strict Path Traversal Protection**: All artifact endpoints (`/api/jobs/:id/artifacts/:path`) resolve paths within the job's directory and reject arbitrary filesystem access.
- **Zero Mock Data**: All metrics, coordinates, drawings, and BOM values come directly from actual engine output files.
- **Persistent State**: Jobs are stored on the filesystem under `jobs/<job_id>/` with `job.json`, `input/`, `output/`, and `logs/`. Refreshing the browser preserves full job status and history.

---

## 4. Verification & Testing

Run the full backend and engine regression test suite:

```powershell
# Run all tests
.\my_venv\Scripts\python.exe -m unittest discover -s tests

# Prove end-to-end DXF upload and execution
.\my_venv\Scripts\python.exe tests/prove_phase2_real_engine.py

# Verify Next.js build
cd web
npm run build
```
