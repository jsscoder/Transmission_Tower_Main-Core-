"""LayerScanner: Semantic layer discovery and classification for arbitrary DXFs.

Replaces brittle exact-string layer checks with fuzzy semantic classification,
allowing the pipeline to operate seamlessly across varying CAD drafting conventions
(e.g., '23_Member designation' vs '23_MEMBER_DESIGNATION' vs 'BM').
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Sequence


def normalize_layer_name(name: str) -> str:
    """Strips all non-alphanumeric characters and lowercases."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# Semantic keyword rules for layer categorization
ROLE_PATTERNS = {
    "DESIGNATION": [
        r"23.*member.*desig",
        r"member.*desig",
        r"desig",
        r"backmark",
        r"^bm$",
        r"^bm\d*$",
    ],
    "MEMBERS": [
        r"3.*member",
        r"^member",
        r"^leg",
        r"^part",
        r"^l10$",
        r"^plt$",
        r"^steel$",
    ],
    "BOLTS": [
        r"5.*bolt",
        r"bolt",
        r"^bn\d*$",
        r"^bnt$",
        r"hole",
        r"circle",
    ],
    "DIMENSIONS": [
        r"dim",
        r"dimension",
    ],
    "BORDER_TITLE": [
        r"frame",
        r"border",
        r"title.*block",
        r"cartus",
        r"sheet",
    ],
}


@dataclass
class LayerClassification:
    all_layers: list[str] = field(default_factory=list)
    designation_layers: list[str] = field(default_factory=list)
    member_layers: list[str] = field(default_factory=list)
    bolt_layers: list[str] = field(default_factory=list)
    dimension_layers: list[str] = field(default_factory=list)
    border_layers: list[str] = field(default_factory=list)

    @property
    def primary_designation_layer(self) -> str:
        """Returns the most likely designation layer name, defaulting to '23_Member designation'."""
        if self.designation_layers:
            return self.designation_layers[0]
        return "23_Member designation"

    @property
    def primary_member_layer(self) -> str:
        """Returns the most likely member line layer name, defaulting to '3_Members'."""
        if self.member_layers:
            return self.member_layers[0]
        return "3_Members"

    @property
    def primary_bolt_layer(self) -> str:
        """Returns the primary bolt layer, defaulting to '5_Bolts'."""
        if self.bolt_layers:
            return self.bolt_layers[0]
        return "5_Bolts"

    def is_role(self, layer_name: str, role: str) -> bool:
        """Check whether a given raw layer name matches a specified role."""
        role_upper = role.upper()
        norm = normalize_layer_name(layer_name)
        if role_upper == "DESIGNATION":
            return any(normalize_layer_name(l) == norm for l in self.designation_layers)
        elif role_upper == "MEMBERS":
            return any(normalize_layer_name(l) == norm for l in self.member_layers)
        elif role_upper == "BOLTS":
            return any(normalize_layer_name(l) == norm for l in self.bolt_layers)
        elif role_upper == "DIMENSIONS":
            return any(normalize_layer_name(l) == norm for l in self.dimension_layers)
        elif role_upper in ("BORDER", "BORDER_TITLE"):
            return any(normalize_layer_name(l) == norm for l in self.border_layers)
        return False

    def to_dict(self) -> dict:
        return {
            "all_layers": self.all_layers,
            "designation_layers": self.designation_layers,
            "member_layers": self.member_layers,
            "bolt_layers": self.bolt_layers,
            "dimension_layers": self.dimension_layers,
            "border_layers": self.border_layers,
            "primary": {
                "designation": self.primary_designation_layer,
                "members": self.primary_member_layer,
                "bolts": self.primary_bolt_layer,
            },
        }


def classify_layers(layers: Sequence[str]) -> LayerClassification:
    """Classifies a list of DXF layer names into semantic categories."""
    res = LayerClassification(all_layers=list(layers))
    
    for layer in layers:
        layer_lower = layer.lower()
        norm = normalize_layer_name(layer)
        
        # Check DESIGNATION
        for pat in ROLE_PATTERNS["DESIGNATION"]:
            if re.search(pat, layer_lower) or re.search(pat, norm):
                if layer not in res.designation_layers:
                    res.designation_layers.append(layer)
                break

        # Check MEMBERS
        for pat in ROLE_PATTERNS["MEMBERS"]:
            if re.search(pat, layer_lower) or re.search(pat, norm):
                if layer not in res.member_layers:
                    res.member_layers.append(layer)
                break

        # Check BOLTS
        for pat in ROLE_PATTERNS["BOLTS"]:
            if re.search(pat, layer_lower) or re.search(pat, norm):
                if layer not in res.bolt_layers:
                    res.bolt_layers.append(layer)
                break

        # Check DIMENSIONS
        for pat in ROLE_PATTERNS["DIMENSIONS"]:
            if re.search(pat, layer_lower) or re.search(pat, norm):
                if layer not in res.dimension_layers:
                    res.dimension_layers.append(layer)
                break

        # Check BORDER_TITLE
        for pat in ROLE_PATTERNS["BORDER_TITLE"]:
            if re.search(pat, layer_lower) or re.search(pat, norm):
                if layer not in res.border_layers:
                    res.border_layers.append(layer)
                break

    # Prioritize canonical layers first if present
    def _prioritize(lst, canonical_name):
        canon_norm = normalize_layer_name(canonical_name)
        return sorted(lst, key=lambda x: (normalize_layer_name(x) != canon_norm, len(x)))

    res.designation_layers = _prioritize(res.designation_layers, "23_Member designation")
    res.member_layers = _prioritize(res.member_layers, "3_Members")
    res.bolt_layers = _prioritize(res.bolt_layers, "5_Bolts")

    return res


def scan_dxf_layers(doc) -> LayerClassification:
    """Inspects an ezdxf document and returns its LayerClassification."""
    names = [l.dxf.name for l in doc.layers]
    return classify_layers(names)
