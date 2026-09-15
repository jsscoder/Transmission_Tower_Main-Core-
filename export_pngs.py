"""Export generated DXF drawings to PNG images using Matplotlib backend."""
from __future__ import annotations

from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend


def export_dxf_to_png(dxf_path: str | Path, png_path: str | Path, dpi: int = 150):
    dxf_path = Path(dxf_path)
    png_path = Path(png_path)
    png_path.parent.mkdir(parents=True, exist_ok=True)

    doc = ezdxf.readfile(dxf_path)
    figsize = (16.54, 11.69)  # A3 landscape

    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal", "datalim")

    ctx = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    frontend = Frontend(ctx, backend)
    frontend.draw_layout(doc.modelspace(), finalize=True)

    fig.savefig(str(png_path), format="png", dpi=dpi, facecolor="white")
    plt.close(fig)
    return png_path


if __name__ == "__main__":
    shop_dir = Path("pipeline_out/shop_drawings")
    vis_dir = Path("pipeline_out/visual_comparison")
    vis_dir.mkdir(parents=True, exist_ok=True)

    for bm in ("37", "44", "45", "46"):
        dxf_file = shop_dir / f"429B{bm}.dxf"
        if dxf_file.exists():
            out_png = vis_dir / f"generated_429B{bm}.png"
            export_dxf_to_png(dxf_file, out_png)
            print(f"Exported: {out_png}")
