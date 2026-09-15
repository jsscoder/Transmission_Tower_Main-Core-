"""
tower_pipeline.py
==================
Deterministic, ezdxf-based pipeline for a 110kV tower ASSEMBLY drawing
(WO_429_LLA1_...). No neural nets, no invented geometry, no simulated
metrics -- every number in the output comes from a real DXF entity.

WHAT THIS FILE CAN AND CANNOT DO (read this before you run it)
-----------------------------------------------------------------
This DXF is the tower ELEVATION / erection drawing -- key plan, transverse
and longitudinal faces, bolt schedule. It calls out every member with a
backmark ("21", "22H", "23H", ...) and a section+length ("L90x90x6..1849"),
and that data is real and extractable (Stage 1 below).

It does NOT contain per-member shop-detail geometry: hole spacings, end
cuts, gauge lines, etc. (the kind of thing shown on a BELT/PLATE/CLEAT/
DIAGONAL/LEG fabrication sketch) are not encoded anywhere in this file.
Stage 2 below gives you a "locator" crop -- literally where in the tower
elevation a backmark is called out -- which is genuine and useful, but it
is not a substitute for the real shop drawing. If/when you have the real
per-member shop DXFs, `render_member_dxf()` will batch-render those to
clean PNGs.

STAGES
------
1. extract_member_schedule(dxf_path)
   Pairs each backmark label with its nearest section+length callout on
   the "23_Member designation" layer. Non-backmark annotation text
   ("N.S.", "CLT.", "PACK PLATE", "BTB LEVEL", ...) is filtered out of the
   candidate pool so it can't steal a match that belongs to a real
   backmark -- this was silently wrong in the first version of this
   script and is the reason "26H" used to fail.

   A second pass infers symmetric duplicates: if a backmark has no nearby
   description of its own but an adjacent backmark (e.g. 27H for 26H --
   same numeric root, same suffix) does, and the drawing only labelled
   one of the pair (common when QTY=2 symmetric members share one
   dimension callout), it inherits that section/length with
   `inferred=True` so you can see it's not a direct match.

2. render_locator_crops(dxf_path, mark_positions, out_dir)
   Crops a window of the real rasterised assembly drawing around each
   backmark's actual text position and burns a label strip (backmark /
   section / length / inferred flag) onto it with PIL. Works even for
   backmarks with no schedule match, since it uses the raw text location,
   not the paired description.

3. render_member_dxf(dxf_path, png_path)
   For real per-member shop DXFs, if/when you have them.

USAGE
-----
    python tower_pipeline.py --dxf "C:\\path\\to\\your.dxf"

Everything is written under a `pipeline_out/` folder created next to this
script (or wherever --out points), on your local machine -- nothing is
written anywhere else.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from pathlib import Path

DESIGNATION_LAYER = "23_Member designation"

# A real backmark looks like "21", "22H", "375H" -- digits, optional
# 1-2 letter suffix. This excludes annotation text on the same layer
# ("N.S.", "CLT.", "F.S.", "PACK PLATE", "BTB LEVEL", "PLT.", "%%UP1", ...)
# that used to get greedily matched to descriptions it had nothing to do
# with.
BACKMARK_RE = re.compile(r"^\d{1,4}[A-Z]{0,2}$")


def _require(pkg_name, pip_name=None):
    try:
        __import__(pkg_name)
    except ImportError:
        sys.exit(
            f"[FATAL] Missing dependency '{pkg_name}'. Install with:\n"
            f"    pip install {pip_name or pkg_name}"
        )


_require("ezdxf")
import ezdxf  # noqa: E402
from ezdxf import bbox, recover  # noqa: E402


# ---------------------------------------------------------------------------
# Stage 1: member schedule extraction
# ---------------------------------------------------------------------------
def _base_num(mark: str):
    m = re.match(r"^(\d+)", mark)
    return m.group(1) if m else None


def _suffix(mark: str):
    m = re.match(r"^\d+([A-Z]*)$", mark)
    return m.group(1) if m else ""


def extract_member_schedule(dxf_path: str) -> dict:
    """Returns {backmark: {section, length_mm, count, locations,
    match_distance_mm, inferred}}. `locations` always has every raw
    occurrence of that backmark's text, even if it never got a
    description match, so locator crops still work."""
    doc, auditor = recover.readfile(dxf_path)
    if auditor.has_errors:
        raise RuntimeError(f"DXF has structural errors: {auditor.errors}")
    msp = doc.modelspace()

    texts = [
        (e.dxf.text.strip(), e.dxf.insert.x, e.dxf.insert.y)
        for e in msp.query("TEXT")
        if e.dxf.layer == DESIGNATION_LAYER
    ]

    descs = [(t, x, y) for t, x, y in texts if ".." in t]
    marks = [(t, x, y) for t, x, y in texts if ".." not in t and BACKMARK_RE.match(t)]

    # every raw occurrence of every backmark, used for locator crops even
    # when a backmark never gets a direct description match
    all_positions = {}
    for t, x, y in marks:
        all_positions.setdefault(t, []).append((round(x, 1), round(y, 1)))

    used = set()
    schedule = {}
    for dt, dx, dy in descs:
        best_i, best_d = None, float("inf")
        for i, (mt, mx, my) in enumerate(marks):
            if i in used:
                continue
            d = math.hypot(dx - mx, dy - my)
            if d < best_d:
                best_d, best_i = d, i
        if best_i is None:
            continue
        used.add(best_i)
        mark = marks[best_i][0]
        section, length = dt.rsplit("..", 1)
        entry = schedule.setdefault(
            mark,
            {
                "section": section,
                "length_mm": length,
                "count": 0,
                "locations": list(all_positions.get(mark, [])),
                "match_distance_mm": round(best_d, 1),
                "inferred": False,
            },
        )
        entry["count"] += 1

    # Second pass: infer symmetric duplicates for backmarks that were seen
    # on this layer but never matched a description of their own. Pick the
    # partner by ACTUAL SPATIAL DISTANCE between text positions, not by
    # numeric ID closeness -- ID closeness is ambiguous (e.g. 26H sits
    # exactly 1 away from both 25H and 27H numerically, but 25H is a
    # different member on the other side of the tower; only 27H is really
    # next to it on the drawing, ~70mm away vs. ~5200mm for 25H).
    unmatched = [m for m in all_positions if m not in schedule]
    for mark in unmatched:
        suf = _suffix(mark)
        my_positions = all_positions[mark]
        best_d, nearest = float("inf"), None
        for other, entry in schedule.items():
            if other == mark or _suffix(other) != suf:
                continue
            for ox, oy in entry["locations"]:
                for mx, my in my_positions:
                    d = math.hypot(mx - ox, my - oy)
                    if d < best_d:
                        best_d, nearest = d, other
        if nearest is None:
            continue
        src = schedule[nearest]
        schedule[mark] = {
            "section": src["section"],
            "length_mm": src["length_mm"],
            "count": 0,
            "locations": list(all_positions.get(mark, [])),
            "match_distance_mm": None,
            "inferred": True,
            "inferred_from": nearest,
        }

    return schedule


def schedule_to_rows(schedule: dict):
    rows = []
    for mark in sorted(schedule, key=lambda m: (len(m), m)):
        e = schedule[mark]
        rows.append(
            {
                "backmark": mark,
                "section": e["section"],
                "length_mm": e["length_mm"],
                "callout_count": e["count"],
                "inferred": e["inferred"],
                "inferred_from": e.get("inferred_from", ""),
            }
        )
    return rows


def write_schedule(schedule: dict, out_dir: Path):
    rows = schedule_to_rows(schedule)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "member_schedule.json", "w") as f:
        json.dump(rows, f, indent=2)
    with open(out_dir / "member_schedule.csv", "w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["backmark", "section", "length_mm", "callout_count", "inferred", "inferred_from"],
        )
        w.writeheader()
        w.writerows(rows)
    return rows


# ---------------------------------------------------------------------------
# Stage 2: locator crops (real geometry, cropped -- not invented)
# ---------------------------------------------------------------------------
def render_full_assembly(dxf_path: str, png_path: Path, dpi: int = 300):
    _require("matplotlib")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    doc, _ = recover.readfile(dxf_path)
    msp = doc.modelspace()
    ext = bbox.extents(msp)
    w = ext.extmax.x - ext.extmin.x
    h = ext.extmax.y - ext.extmin.y

    fig = plt.figure(figsize=(w / 500, h / 500))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(ext.extmin.x, ext.extmax.x)
    ax.set_ylim(ext.extmin.y, ext.extmax.y)
    Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(msp, finalize=True)
    ax.set_axis_off()
    fig.savefig(png_path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)


def render_locator_crops(dxf_path: str, schedule: dict, out_dir: Path, marks=None, margin=700, dpi=300):
    """Crop a locator window around each backmark's real text position and
    burn a label strip on it. Uses raw positions, so it still works for
    backmarks with no schedule match."""
    _require("PIL", "pillow")
    from PIL import Image, ImageDraw, ImageFont

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    full_png = out_dir / "_full_assembly.png"
    xmin, ymin, xmax, ymax = render_full_assembly(dxf_path, full_png, dpi=dpi)

    img = Image.open(full_png).convert("RGB")
    W, H = img.size
    world_w, world_h = xmax - xmin, ymax - ymin

    def world_to_px(x, y):
        px = (x - xmin) / world_w * W
        py = H - (y - ymin) / world_h * H
        return px, py

    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font = ImageFont.load_default()

    target_marks = marks if marks is not None else sorted(schedule, key=lambda m: (len(m), m))
    results = {}
    for mark in target_marks:
        entry = schedule.get(mark)
        if not entry or not entry["locations"]:
            results[mark] = None
            continue
        wx, wy = entry["locations"][0]
        px1, py1 = world_to_px(wx - margin, wy + margin)
        px2, py2 = world_to_px(wx + margin, wy - margin)
        left, top = max(0, int(min(px1, px2))), max(0, int(min(py1, py2)))
        right, bottom = min(W, int(max(px1, px2))), min(H, int(max(py1, py2)))
        if right <= left or bottom <= top:
            results[mark] = None
            continue
        crop = img.crop((left, top, right, bottom))

        label_h = 34
        labeled = Image.new("RGB", (crop.width, crop.height + label_h), "white")
        labeled.paste(crop, (0, label_h))
        draw = ImageDraw.Draw(labeled)
        tag = " (inferred)" if entry.get("inferred") else ""
        caption = f"{mark}  |  {entry['section']}  L={entry['length_mm']}mm{tag}"
        draw.rectangle([0, 0, labeled.width, label_h], fill=(30, 30, 30))
        draw.text((6, 7), caption, fill="white", font=font)

        out_path = out_dir / f"locator_{mark}.png"
        labeled.save(out_path)
        results[mark] = str(out_path)
    return results


# ---------------------------------------------------------------------------
# Stage 3: generic per-member shop-drawing renderer (for real member DXFs)
# ---------------------------------------------------------------------------
def render_member_dxf(dxf_path: str, png_path: Path, dpi: int = 300, figsize=(11, 8)):
    _require("matplotlib")
    import matplotlib.pyplot as plt
    from ezdxf.addons.drawing import Frontend, RenderContext
    from ezdxf.addons.drawing.matplotlib import MatplotlibBackend

    doc, _ = recover.readfile(dxf_path)
    msp = doc.modelspace()
    fig = plt.figure(figsize=figsize)
    ax = fig.add_axes([0.02, 0.02, 0.96, 0.96])
    Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(msp, finalize=True)
    ax.set_axis_off()
    fig.savefig(png_path, dpi=dpi, facecolor="white")
    plt.close(fig)
    return str(png_path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    script_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description="Tower assembly DXF pipeline")
    parser.add_argument("--dxf", required=True, help="Path to the assembly .dxf file (NOT .dwg)")
    parser.add_argument(
        "--out",
        default=str(script_dir / "pipeline_out"),
        help="Output folder (default: pipeline_out next to this script)",
    )
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--margin", type=float, default=700, help="Locator crop half-window, in DXF units")
    args = parser.parse_args()

    dxf_path = Path(args.dxf)
    if not dxf_path.exists():
        sys.exit(f"[FATAL] File not found: {dxf_path}")
    if dxf_path.suffix.lower() != ".dxf":
        sys.exit(
            f"[FATAL] '{dxf_path.suffix}' is not a .dxf file. ezdxf cannot read .dwg "
            f"(AutoCAD's binary format). Export/Save As DXF first."
        )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/2] Extracting member schedule from {dxf_path.name} ...")
    schedule = extract_member_schedule(str(dxf_path))
    rows = write_schedule(schedule, out_dir)
    n_direct = sum(1 for r in rows if not r["inferred"])
    n_inferred = sum(1 for r in rows if r["inferred"])
    print(f"      {len(rows)} backmarks total  ({n_direct} direct match, {n_inferred} inferred)")
    print(f"      -> {out_dir / 'member_schedule.csv'}")
    print(f"      -> {out_dir / 'member_schedule.json'}")

    print(f"[2/2] Rendering locator crops for all {len(rows)} backmarks ...")
    locators_dir = out_dir / "locators"
    crops = render_locator_crops(str(dxf_path), schedule, locators_dir, margin=args.margin, dpi=args.dpi)
    ok = sum(1 for v in crops.values() if v)
    print(f"      {ok}/{len(crops)} locator crops written -> {locators_dir}")
    for m, p in crops.items():
        status = p if p else "NO POSITION FOUND ON DRAWING"
        print(f"        {m:8s} -> {status}")

    print(f"\n[DONE] Everything saved locally under: {out_dir}")


if __name__ == "__main__":
    main()

    