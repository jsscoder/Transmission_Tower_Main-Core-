"""ScaleContext: Scale-adaptive geometric calculations and drawing extents.

Provides scale-invariant coordinate normalizations and dynamic geometric
tolerances across transmission tower drawings of varying physical spans
(e.g., 10m to 50m) and arbitrary CAD origin offsets.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
import ezdxf
from ezdxf import bbox, recover


# Standard baseline reference diagonal (WO_429: 12.75m x 8.91m -> ~15,550 mm)
REFERENCE_DIAGONAL_MM = 15550.0


@dataclass(frozen=True)
class ScaleContext:
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    width_mm: float
    height_mm: float
    diagonal_mm: float
    scale_factor: float

    @classmethod
    def from_extents(cls, min_x: float, min_y: float, max_x: float, max_y: float) -> ScaleContext:
        w = max(1.0, float(max_x - min_x))
        h = max(1.0, float(max_y - min_y))
        diag = math.hypot(w, h)
        sf = max(0.5, min(5.0, diag / REFERENCE_DIAGONAL_MM))
        return cls(
            min_x=float(min_x),
            min_y=float(min_y),
            max_x=float(max_x),
            max_y=float(max_y),
            width_mm=w,
            height_mm=h,
            diagonal_mm=diag,
            scale_factor=round(sf, 3),
        )

    @classmethod
    def from_dxf(cls, dxf_path: str | Path) -> ScaleContext:
        doc, auditor = recover.readfile(str(dxf_path))
        if auditor.has_errors:
            # Fall back to standard read if auditor flags recoverable issues
            pass
        msp = doc.modelspace()
        ext = bbox.extents(msp)
        if ext is None:
            # Fallback default if bounding box is uncomputable
            return cls.from_extents(0.0, 0.0, 12000.0, 9000.0)
        return cls.from_extents(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)

    @property
    def joint_cluster_tol_mm(self) -> float:
        """Endpoint clustering tolerance for physical joint nodes."""
        return round(35.0 * self.scale_factor, 1)

    @property
    def joint_group_radius_mm(self) -> float:
        """Search radius between callout group center and candidate joints."""
        return round(350.0 * self.scale_factor, 1)

    @property
    def callout_search_radius_mm(self) -> float:
        """Search radius around member leader/anchor points for callout groups."""
        return round(450.0 * self.scale_factor, 1)

    @property
    def joint_decay_scale_mm(self) -> float:
        """Exponential decay distance scale for joint-to-group proximity."""
        return round(180.0 * self.scale_factor, 1)

    @property
    def anchor_decay_scale_mm(self) -> float:
        """Exponential decay distance scale for anchor-to-group proximity."""
        return round(140.0 * self.scale_factor, 1)

    @property
    def max_label_distance_mm(self) -> float:
        """Maximum distance between designation label text and member geometry chain."""
        return round(800.0 * self.scale_factor, 1)

    @property
    def member_search_radius_mm(self) -> float:
        """Perpendicular tolerance when searching for assembly circle bolts along member axis."""
        return round(80.0 * self.scale_factor, 1)

    def adaptive_locator_margin(self, member_length_mm: float | None = None) -> float:
        """Computes a scale-adaptive visual margin (in mm) for member locator crops.
        Ensures the crop window captures both the text callout and the member span,
        avoiding tiny thumbnails on large tower assemblies.
        """
        if member_length_mm and member_length_mm > 0:
            # Frame at least 35% of member length or scaled minimum
            base_margin = max(600.0 * self.scale_factor, min(3000.0 * self.scale_factor, 0.35 * member_length_mm))
        else:
            base_margin = 700.0 * self.scale_factor
        return round(base_margin, 1)

    def world_to_normalized(self, x: float, y: float) -> tuple[float, float]:
        """Maps world CAD coordinates into [0.0, 1.0] unit square relative to drawing extents."""
        nx = (x - self.min_x) / self.width_mm
        ny = (y - self.min_y) / self.height_mm
        return (nx, ny)

    def to_dict(self) -> dict:
        return {
            "extents": {
                "min_x": self.min_x,
                "min_y": self.min_y,
                "max_x": self.max_x,
                "max_y": self.max_y,
            },
            "width_mm": self.width_mm,
            "height_mm": self.height_mm,
            "diagonal_mm": round(self.diagonal_mm, 1),
            "scale_factor": self.scale_factor,
            "adaptive_tolerances": {
                "joint_cluster_tol_mm": self.joint_cluster_tol_mm,
                "joint_group_radius_mm": self.joint_group_radius_mm,
                "callout_search_radius_mm": self.callout_search_radius_mm,
                "joint_decay_scale_mm": self.joint_decay_scale_mm,
                "anchor_decay_scale_mm": self.anchor_decay_scale_mm,
                "max_label_distance_mm": self.max_label_distance_mm,
                "member_search_radius_mm": self.member_search_radius_mm,
            },
        }
