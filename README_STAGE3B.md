# Stage 3B / Phase A–D foundation

The original Stage 3B fixed-code package established joint topology and conservative connection inference. The supplied roadmap now drives the next implementation step: constrained FLAT/plate geometry matching, then an explicit engineering-design input boundary and a native-DXF drawing generator.

## What changed in this build

- Added `member_geometry.py`: constrained LINE-chain candidates using section width, orientation, endpoint connectivity, internal-gap limits and schedule length.
- Reworked `shop_pipeline.py` to consume those candidates and expose multiple candidates plus an evidence status.
- Reworked `connection_topology.py` to use chain endpoints and build the joint graph first.
- Reworked `connection_resolver.py` so E1/E2 ordering is no longer mapped blindly to topology A/B. Geometry determines the nearest physical end. Shared joint groups remain REVIEW/candidate evidence rather than being duplicated as fabrication data.
- Added `design_rules.py`: explicit project-standard validation and approved pitch-pattern expansion.
- Added `shop_drawing.py`: editable native DXF generator from explicit engineering design input.
- Added `bom.py`: job-level BOM aggregation.
- Added `make_design_template.py` and `validate_design.py`.
- Extended `inference.py` with optional generation/BOM stages while keeping extraction usable without design input.

## What is deliberately NOT claimed

The roadmap's validation says the old FLAT matcher was wrong for 44/45/46 and that the bolt-count source is a genuine data gap. The new constrained matcher is the correct engineering direction, but it must still be run against the real WO_429 DXF and real shop drawings before it is called closed. No threshold is lowered just to produce AUTO decisions.

The shop drawing generator does not invent connection design. It requires explicit holes or an explicit approved pitch pattern plus an explicit detailing-rules file.
