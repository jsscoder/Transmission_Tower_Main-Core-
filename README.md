# Transmission Tower — DXF → Shop Drawing Core

This repository is the production-oriented core of the transmission-tower automation pipeline. It is intentionally **data/evidence first** and is structured so a future backend/API and UI can call the same Python functions.

## What is reliable vs. what is not

The supplied roadmap reports that Stage 1 member schedule and locators are reliable, native B1 callout extraction is reliable, Stage 3 candidate confidence is not yet ground-truth validated, and the previous nearest-LINE Stage 4 geometry was reliable for angle members but unreliable for FLAT members. The roadmap explicitly says the FLAT problem must be fixed before downstream automation is trusted. See `README_STAGE3B.md` and the roadmap supplied with this project.

This version therefore changes the geometry stage to a **constrained chain candidate model** and exposes its evidence/status instead of pretending the problem is solved. Run the real validation fixture before promoting the FLAT matcher to AUTO.

## Pipeline

1. **Member schedule + locators** — `tower.py`
2. **Native B1 bolt callouts + grouping** — `connection_annotations.py`
3. **Leader/anchor connection candidates** — `connection_association.py`
4. **Constrained member geometry + joint graph** — `member_geometry.py`, `shop_pipeline.py`, `connection_topology.py`
5. **Joint-first conservative resolution** — `connection_resolver.py`
6. **Shop drawing generation** — `shop_drawing.py` (requires approved engineering design input)
7. **Job BOM** — `bom.py`

The four real shop drawings in `fixtures/shop_ground_truth.json` remain **validation only**. They are never read by production extraction or generation.

## Setup

```powershell
python -m venv my_venv
.\\my_venv\\Scripts\\Activate.ps1
python -m pip install -r req.txt
```

## Run extraction/inference only

```powershell
python inference.py --dxf "path\\assembly.dxf" --out pipeline_out
```

This creates schedule, locators, native callout groups, candidate associations, constrained geometry evidence, joint topology, resolver decisions and `pipeline_summary.json`.

## Create an engineering-input template

```powershell
python make_design_template.py --dxf "path\\assembly.dxf" --out connection_design.template.json
```

The template copies only Stage-1 facts. **Bolt counts, hole positions, gauges, pitches, edge distances and end details remain engineering inputs.**

## Generate editable shop DXFs + BOM

First populate `connection_design.json` from an approved engineering source and provide an approved project rules file.

```powershell
python validate_design.py --design-input connection_design.json --rules design_rules.json
python inference.py --dxf "path\\assembly.dxf" --out pipeline_out --design-input connection_design.json --rules design_rules.json --generate
```

Generated drawings are native/editable DXF files under `pipeline_out/shop_drawings/`. The BOM is under `pipeline_out/job_bom.csv` and `job_bom.json`.

### Design input contract

```json
{
  "members": [
    {
      "backmark": "37",
      "section": "L50x50x5",
      "length_mm": 2294,
      "qty": 2,
      "ends": {
        "E1": {
          "holes": [
            {"along_mm": 35, "hole_diameter_mm": 11.5, "edge_distance_mm": 25, "nominal_bolt_diameter_mm": 10}
          ]
        },
        "E2": {"holes": []}
      }
    }
  ]
}
```

An end may instead use an explicit approved pitch pattern (`count`, hole diameter, nominal bolt diameter, start position, pitch, edge distance). The generator validates that pattern against the supplied rules before creating the DXF.

## Important engineering boundary

An assembly drawing can contain joint-level bolt annotations. That does **not** prove how the joint's total bolts are partitioned across individual members. The resolver therefore reports candidates/review rather than copying a whole shared group onto every member.

The project-specific detailing standard must also be supplied explicitly. `design_rules.example.json` is a **placeholder example**, not an approved engineering standard.

The roadmap identifies the bolt-count-per-connection source as the remaining data gap that cannot be solved by more geometry inference. Until that source exists, automatic fabrication output should not be considered engineering-authoritative.

## Future backend/UI contract

Keep these functions as the service boundary:

- `inference.run_pipeline(...)`
- `tower.extract_member_schedule(...)`
- `connection_annotations.extract_bolt_callouts(...)`
- `connection_association.associate(...)`
- `shop_pipeline.extract_member_evidence(...)`
- `connection_topology.build_topology(...)`
- `connection_resolver.resolve(...)`
- `shop_drawing.generate_job(...)`
- `bom.build_bom(...)`

A future backend can expose jobs, files, member review, connection review, approval state and generated DXFs without changing the geometry/generation engine.

## Shop-drawing capacity + regression reporting

For a specific assembly DXF, the reporting layer now separates:

- **Identified shop drawings** — unique backmarks extracted from the member schedule; this is the number of member drawing records the DXF describes.
- **Currently buildable** — identified backmarks that also have approved design-input records with end details. This is the number the generator can actually produce without inventing connection design.
- **Blocked** — identified backmarks missing approved design input or required end information.

Run only the capacity report:

```powershell
python shop_drawing_inventory.py --dxf "path\\assembly.dxf" --out shop_inventory
```

The full pipeline also creates `shop_drawing_inventory.json/.csv`.

### Existing shop JSON vs generated shop JSON

Every generated drawing now receives a sidecar JSON next to its DXF. The canonical comparison fields are:

- drawing ID / backmark
- section
- member length
- quantity
- bolt count per piece
- hole-diameter count per piece
- exact hole positions when both source and generated JSON contain them

The comparator accepts a single JSON file or a directory of JSON files:

```powershell
python compare_shop_json.py --reference "existing_shop_json" --generated "pipeline_out\\shop_drawings" --out comparison
```

Use `--length-tol-mm` and `--position-tol-mm` for project-approved comparison tolerances. Results are written as `shop_json_comparison.json` and `.csv` and classify records as `MATCH`, `MISMATCH`, `MISSING_GENERATED`, `EXTRA_GENERATED`, or `NOT_COMPARABLE`.

The same comparison can be run inside the main pipeline:

```powershell
python inference.py --dxf "path\\assembly.dxf" --out pipeline_out \\
  --design-input connection_design.json --rules design_rules.json --generate \\
  --reference-shop-json-dir "existing_shop_json"
```

### Group / backmark JSON package

The run now produces:

```text
pipeline_out/
├── shop_drawing_inventory.json
├── shop_drawing_inventory.csv
├── json_artifact_manifest.json
├── group_report.json
├── group_report.csv
├── detailed_shop_report.json
├── detailed_shop_report.md
└── grouped_json/
    ├── backmark_37.json
    ├── backmark_44.json
    └── ...
```

`group_report.json` connects backmarks to resolved connection-group IDs. `grouped_json/backmark_<mark>.json` is the backend/UI-friendly per-part package containing schedule data, associated groups, candidate associations and resolver results. `json_artifact_manifest.json` indexes every JSON produced by the run by pipeline stage.

Reference shop JSON is validation-only. It never becomes an inference source.
