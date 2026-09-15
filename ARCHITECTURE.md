# Architecture contract for future backend + UI

## Core principle

The Python core is the engineering engine. A backend should orchestrate jobs and approvals; a UI should display evidence and let an authorized user review/edit explicit design inputs. Neither should duplicate geometry logic.

## Suggested service boundaries

### POST `/jobs`
Create a job with an uploaded assembly DXF.

### POST `/jobs/{id}/infer`
Runs `inference.run_pipeline()` without fabrication generation.

### GET `/jobs/{id}/members`
Reads `member_schedule.csv` + `member_evidence.json`.

### GET `/jobs/{id}/connections/review`
Reads `connection_review.csv` and `resolved_connections.json`.

### PATCH `/jobs/{id}/design`
Stores approved per-member connection design JSON. Every edit should record user, timestamp and revision.

### POST `/jobs/{id}/validate-design`
Runs `validate_design.py` logic.

### POST `/jobs/{id}/generate`
Runs `shop_drawing.generate_job()` and `bom.write_bom()` only after validation/approval.

### GET `/jobs/{id}/drawings`
Returns the generated DXF manifest.

## UI review model

A member review card should show:

- backmark / section / schedule length / schedule occurrence count
- locator image
- selected geometry chain and candidate alternatives
- length error and evidence status
- joint ID and member ends
- nearby native B1 callouts
- resolver confidence/status
- explicit engineering design fields: bolt diameter, hole diameter, count, hole positions, gauge/edge distance
- approval/review state

The UI must visually distinguish **assembly evidence** from **approved fabrication design**. A nearby bolt annotation is evidence, not an automatically approved per-member bolt count.

## Storage model

Keep raw source files immutable. Store pipeline outputs by job/revision:

```text
jobs/<job-id>/source/assembly.dxf
jobs/<job-id>/inference/<revision>/...
jobs/<job-id>/design/<revision>/connection_design.json
jobs/<job-id>/rules/<revision>/design_rules.json
jobs/<job-id>/generated/<revision>/429B*.dxf
```

This makes the future backend auditable and allows regression tests to reproduce exactly which inputs generated a drawing.
