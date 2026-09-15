"""Canonical domain dataclasses and serialization for transmission tower detailing."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from domain.enums import EndId, SectionType, SourceKind, ValidationState


def classify_section(section: str | None) -> SectionType:
    """Classify a raw section string into a canonical SectionType enum."""
    if not section:
        return SectionType.UNKNOWN
    s = str(section).strip().upper().replace(" ", "")
    if s.startswith("HTL"):
        return SectionType.HT_ANGLE
    if s.startswith("L"):
        return SectionType.ANGLE
    if s.startswith("PL"):
        return SectionType.PLATE
    if s.startswith("HT"):
        # Check if it has 3 dimensions like HT 50x50x5
        if re.search(r"\d+\s*[xX]\s*\d+\s*[xX]\s*\d+", s):
            return SectionType.HT_ANGLE
        if any(k in s for k in ("THK", "X", "FLAT")):
            return SectionType.HT_FLAT
    if re.search(r"\d+\s*[xX]\s*\d+\s*[xX]\s*\d+", s):
        return SectionType.ANGLE
    if s.startswith("FLAT") or "THK" in s:
        return SectionType.FLAT
    if re.match(r"^\d+(?:\.\d+)?X\d+", s):
        return SectionType.FLAT
    return SectionType.UNKNOWN


@dataclass
class ProvenanceRecord:
    source_kind: SourceKind = SourceKind.DESIGN_INPUT
    source_id: str = ""
    entity_id: str = ""
    confidence: float = 1.0
    rule_id: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind.value if isinstance(self.source_kind, SourceKind) else str(self.source_kind),
            "source_id": self.source_id,
            "entity_id": self.entity_id,
            "confidence": float(self.confidence),
            "rule_id": self.rule_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | ProvenanceRecord | None) -> ProvenanceRecord | None:
        if not data:
            return None
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            return None
        return cls(
            source_kind=SourceKind.from_str(data.get("source_kind")),
            source_id=str(data.get("source_id", "") or ""),
            entity_id=str(data.get("entity_id", "") or ""),
            confidence=float(data.get("confidence", 1.0) if data.get("confidence") is not None else 1.0),
            rule_id=str(data.get("rule_id", "") or ""),
            notes=str(data.get("notes", "") or ""),
        )


@dataclass
class Hole:
    hole_id: str
    member_backmark: str
    end: str
    along_mm: float
    transverse_mm: float | None = None
    diameter_mm: float = 0.0
    nominal_bolt_diameter_mm: float | None = None
    bolt_size: str | None = None
    edge_distance_mm: float | None = None
    provenance: ProvenanceRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "hole_id": self.hole_id,
            "member_backmark": self.member_backmark,
            "end": self.end,
            "along_mm": float(self.along_mm),
            "transverse_mm": float(self.transverse_mm) if self.transverse_mm is not None else None,
            "diameter_mm": float(self.diameter_mm),
            "hole_diameter_mm": float(self.diameter_mm),
            "nominal_bolt_diameter_mm": float(self.nominal_bolt_diameter_mm) if self.nominal_bolt_diameter_mm is not None else None,
            "bolt_size": self.bolt_size,
            "edge_distance_mm": float(self.edge_distance_mm) if self.edge_distance_mm is not None else None,
            "provenance": self.provenance.to_dict() if hasattr(self.provenance, "to_dict") else self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hole:
        nom_bolt = data.get("nominal_bolt_diameter_mm")
        if nom_bolt is None:
            nom_bolt = data.get("bolt_diameter_mm")
        bolt_size = data.get("bolt_size")
        if not bolt_size and nom_bolt is not None:
            bolt_size = f"M{int(float(nom_bolt))}"

        prov = data.get("provenance")
        if isinstance(prov, ProvenanceRecord):
            provenance = prov
        elif isinstance(prov, dict):
            provenance = ProvenanceRecord.from_dict(prov)
        else:
            provenance = None

        along = data.get("along_mm", 0.0)
        diam = data.get("diameter_mm")
        if diam is None:
            diam = data.get("hole_diameter_mm", 0.0)

        trans = data.get("transverse_mm")
        edge_dist = data.get("edge_distance_mm")

        return cls(
            hole_id=str(data.get("hole_id", "") or f"H_{data.get('member_backmark','')}_{data.get('end','')}_{along}"),
            member_backmark=str(data.get("member_backmark", "") or ""),
            end=str(data.get("end", "") or "UNKNOWN"),
            along_mm=float(along),
            transverse_mm=float(trans) if trans is not None else None,
            diameter_mm=float(diam) if diam is not None else 0.0,
            nominal_bolt_diameter_mm=float(nom_bolt) if nom_bolt is not None else None,
            bolt_size=str(bolt_size) if bolt_size is not None else None,
            edge_distance_mm=float(edge_dist) if edge_dist is not None else None,
            provenance=provenance,
        )


@dataclass
class MemberEnd:
    end_id: str
    coordinate: tuple[float, float] | None = None
    joint_id: str | None = None
    connection_group_id: str | None = None
    holes: list[Hole] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "end_id": self.end_id,
            "coordinate": list(self.coordinate) if self.coordinate else None,
            "joint_id": self.joint_id,
            "connection_group_id": self.connection_group_id,
            "holes": [h.to_dict() if hasattr(h, "to_dict") else h for h in self.holes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], end_id: str = "UNKNOWN", backmark: str = "") -> MemberEnd:
        eid = str(data.get("end_id") or end_id)
        coord = None
        raw_coord = data.get("coordinate")
        if isinstance(raw_coord, (list, tuple)) and len(raw_coord) >= 2:
            coord = (float(raw_coord[0]), float(raw_coord[1]))

        holes: list[Hole] = []
        # Support direct hole list
        if "holes" in data and isinstance(data["holes"], list):
            for i, h in enumerate(data["holes"]):
                if isinstance(h, dict):
                    hd = dict(h)
                    hd.setdefault("end", eid)
                    hd.setdefault("member_backmark", backmark)
                    hd.setdefault("hole_id", f"H_{backmark}_{eid}_{i}")
                    holes.append(Hole.from_dict(hd))
                elif isinstance(h, Hole):
                    holes.append(h)

        # Support pattern definition
        pattern = data.get("pattern")
        if isinstance(pattern, dict) and not holes:
            cnt = int(pattern.get("count", 0) or 0)
            start = float(pattern.get("start_from_end_mm", 0.0) or 0.0)
            pitch = float(pattern.get("pitch_mm", 0.0) or 0.0)
            dia = float(pattern.get("hole_diameter_mm", 0.0) or 0.0)
            nom_bolt = pattern.get("nominal_bolt_diameter_mm")
            edge_dist = pattern.get("edge_distance_mm")
            trans_dist = pattern.get("transverse_mm")
            bolt_sz = f"M{int(float(nom_bolt))}" if nom_bolt is not None else None

            for i in range(cnt):
                pos = start + i * pitch
                holes.append(Hole(
                    hole_id=f"H_{backmark}_{eid}_{i}",
                    member_backmark=backmark,
                    end=eid,
                    along_mm=pos,
                    transverse_mm=float(trans_dist) if trans_dist is not None else None,
                    diameter_mm=dia,
                    nominal_bolt_diameter_mm=float(nom_bolt) if nom_bolt is not None else None,
                    bolt_size=bolt_sz,
                    edge_distance_mm=float(edge_dist) if edge_dist is not None else None,
                ))

        return cls(
            end_id=eid,
            coordinate=coord,
            joint_id=data.get("joint_id"),
            connection_group_id=data.get("connection_group_id"),
            holes=holes,
        )


@dataclass
class Member:
    backmark: str
    section: str
    section_type: SectionType = SectionType.UNKNOWN
    length_mm: float = 0.0
    quantity: int | None = None
    quantity_source: str | None = None
    ends: dict[str, MemberEnd] = field(default_factory=dict)
    status: ValidationState = ValidationState.EXTRACTED
    provenance: ProvenanceRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backmark": self.backmark,
            "section": self.section,
            "section_type": self.section_type.value if isinstance(self.section_type, SectionType) else str(self.section_type),
            "length_mm": float(self.length_mm),
            "quantity": int(self.quantity) if self.quantity is not None else None,
            "qty": int(self.quantity) if self.quantity is not None else None,
            "quantity_source": self.quantity_source,
            "ends": {k: v.to_dict() if hasattr(v, "to_dict") else v for k, v in sorted(self.ends.items())},
            "status": self.status.value if isinstance(self.status, ValidationState) else str(self.status),
            "provenance": self.provenance.to_dict() if hasattr(self.provenance, "to_dict") else self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Member:
        mark = str(data.get("backmark", "") or "")
        section = str(data.get("section", "") or "")
        sec_type = SectionType.from_str(data.get("section_type"))
        if sec_type == SectionType.UNKNOWN and section:
            sec_type = classify_section(section)

        length = float(data.get("length_mm", 0.0) or 0.0)

        raw_qty = data.get("quantity")
        if raw_qty is None:
            raw_qty = data.get("qty")
        qty = int(raw_qty) if raw_qty is not None and str(raw_qty).strip() != "" else None

        ends_dict: dict[str, MemberEnd] = {}
        raw_ends = data.get("ends") or data.get("connection_ends") or {}
        if isinstance(raw_ends, dict):
            for k, v in raw_ends.items():
                if isinstance(v, dict):
                    ends_dict[k] = MemberEnd.from_dict(v, end_id=k, backmark=mark)
                elif isinstance(v, MemberEnd):
                    ends_dict[k] = v
        elif isinstance(raw_ends, list):
            for i, v in enumerate(raw_ends):
                if isinstance(v, dict):
                    eid = f"E{i+1}"
                    ends_dict[eid] = MemberEnd.from_dict(v, end_id=eid, backmark=mark)

        status = ValidationState.from_str(data.get("status"))
        prov = data.get("provenance")
        if isinstance(prov, ProvenanceRecord):
            provenance = prov
        elif isinstance(prov, dict):
            provenance = ProvenanceRecord.from_dict(prov)
        else:
            provenance = None

        return cls(
            backmark=mark,
            section=section,
            section_type=sec_type,
            length_mm=length,
            quantity=qty,
            quantity_source=data.get("quantity_source"),
            ends=ends_dict,
            status=status,
            provenance=provenance,
        )


@dataclass
class DimensionItem:
    start_mm: float
    end_mm: float
    value_mm: float
    label: str
    level: int = 1
    kind: str = "incremental"

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_mm": float(self.start_mm),
            "end_mm": float(self.end_mm),
            "value_mm": float(self.value_mm),
            "label": self.label,
            "level": int(self.level),
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DimensionItem:
        return cls(
            start_mm=float(data.get("start_mm", 0.0)),
            end_mm=float(data.get("end_mm", 0.0)),
            value_mm=float(data.get("value_mm", 0.0)),
            label=str(data.get("label", "")),
            level=int(data.get("level", 1)),
            kind=str(data.get("kind", "incremental")),
        )


@dataclass
class TitleBlockData:
    job_no: str = ""
    drawing_id: str = ""
    backmark: str = ""
    section: str = ""
    length_mm: float = 0.0
    quantity: int | None = None
    standard: str = ""
    date: str = ""
    rev: str = "0"
    status: str = "APPROVED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_no": self.job_no,
            "drawing_id": self.drawing_id,
            "backmark": self.backmark,
            "section": self.section,
            "length_mm": float(self.length_mm),
            "quantity": int(self.quantity) if self.quantity is not None else None,
            "standard": self.standard,
            "date": self.date,
            "rev": self.rev,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TitleBlockData:
        if not data or not isinstance(data, dict):
            return cls()
        raw_qty = data.get("quantity")
        if raw_qty is None:
            raw_qty = data.get("qty")
        return cls(
            job_no=str(data.get("job_no", "") or ""),
            drawing_id=str(data.get("drawing_id", "") or ""),
            backmark=str(data.get("backmark", "") or ""),
            section=str(data.get("section", "") or ""),
            length_mm=float(data.get("length_mm", 0.0) or 0.0),
            quantity=int(raw_qty) if raw_qty is not None else None,
            standard=str(data.get("standard", "") or ""),
            date=str(data.get("date", "") or ""),
            rev=str(data.get("rev", "0") or "0"),
            status=str(data.get("status", "APPROVED") or "APPROVED"),
        )


@dataclass
class ShopDrawingModel:
    drawing_id: str
    backmark: str
    member: Member
    holes: list[Hole] = field(default_factory=list)
    dimension_chains: list[list[DimensionItem]] = field(default_factory=list)
    end_section: dict[str, Any] = field(default_factory=dict)
    title_block: TitleBlockData = field(default_factory=TitleBlockData)
    status: ValidationState = ValidationState.READY
    provenance: ProvenanceRecord | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "drawing_id": self.drawing_id,
            "backmark": self.backmark,
            "member": self.member.to_dict() if hasattr(self.member, "to_dict") else self.member,
            "holes": [h.to_dict() if hasattr(h, "to_dict") else h for h in self.holes],
            "dimension_chains": [
                [item.to_dict() if hasattr(item, "to_dict") else item for item in chain]
                for chain in self.dimension_chains
            ],
            "end_section": self.end_section,
            "title_block": self.title_block.to_dict() if hasattr(self.title_block, "to_dict") else self.title_block,
            "status": self.status.value if isinstance(self.status, ValidationState) else str(self.status),
            "provenance": self.provenance.to_dict() if hasattr(self.provenance, "to_dict") else self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShopDrawingModel:
        mem_data = data.get("member")
        if isinstance(mem_data, Member):
            member = mem_data
        elif isinstance(mem_data, dict):
            member = Member.from_dict(mem_data)
        else:
            # Reconstruct member from top-level fields if member sub-dict omitted
            member = Member.from_dict(data)

        holes: list[Hole] = []
        raw_holes = data.get("holes")
        if raw_holes is None:
            raw_holes = data.get("hole_positions")
        if not raw_holes and member:
            raw_holes = [h for end in member.ends.values() for h in end.holes]

        if raw_holes:
            for h in raw_holes:
                if isinstance(h, Hole):
                    holes.append(h)
                elif isinstance(h, dict):
                    holes.append(Hole.from_dict(h))

        chains: list[list[DimensionItem]] = []
        for ch in data.get("dimension_chains", []):
            chain_items = []
            if isinstance(ch, list):
                for item in ch:
                    if isinstance(item, DimensionItem):
                        chain_items.append(item)
                    elif isinstance(item, dict):
                        chain_items.append(DimensionItem.from_dict(item))
            chains.append(chain_items)

        if not chains and member and member.length_mm > 0 and holes:
            try:
                chains = build_dimension_chains(member.length_mm, holes)
            except Exception:
                chains = []

        tb = data.get("title_block")
        if isinstance(tb, TitleBlockData):
            title_block = tb
        elif isinstance(tb, dict):
            title_block = TitleBlockData.from_dict(tb)
        else:
            standard = ""
            gen = data.get("generator")
            if isinstance(gen, dict):
                standard = gen.get("standard", "")
            raw_qty = data.get("quantity")
            if raw_qty is None:
                raw_qty = data.get("qty", member.quantity)
            try:
                qty_val = int(raw_qty) if raw_qty is not None else None
            except (TypeError, ValueError):
                qty_val = None
            title_block = TitleBlockData(
                job_no=str(data.get("job_no", "") or data.get("job", "") or ""),
                drawing_id=str(data.get("drawing_id", "") or f"429B{member.backmark}"),
                backmark=member.backmark,
                section=member.section,
                length_mm=member.length_mm,
                quantity=qty_val,
                standard=standard,
                status=data.get("status", "APPROVED"),
            )

        prov = data.get("provenance")
        provenance = prov if isinstance(prov, ProvenanceRecord) else (
            ProvenanceRecord.from_dict(prov) if isinstance(prov, dict) else None
        )

        return cls(
            drawing_id=str(data.get("drawing_id", "") or f"429B{member.backmark}"),
            backmark=str(data.get("backmark", "") or member.backmark),
            member=member,
            holes=holes,
            dimension_chains=chains,
            end_section=dict(data.get("end_section", {}) or {}),
            title_block=title_block,
            status=ValidationState.from_str(data.get("status")),
            provenance=provenance,
        )


def build_dimension_chains(
    length_mm: float,
    holes: list[Hole] | list[dict[str, Any]] | list[float],
    gauge_mm: float | None = None,
) -> list[list[DimensionItem]]:
    """Build canonical level-1 incremental, level-2 overall, and optional level-3 gauge dimension chains.
    
    Guarantees:
    - Overall dimension equals member overall length (length_mm), NEVER max(hole positions).
    - Enforces the dimension chain rule: all holes must satisfy 0 < pos < length_mm.
    - Last hole coordinate != member overall length (x_last < length_mm).
    - Level 3 represents transverse gauge dimension from heel/edge if gauge_mm is provided.
    """
    length = float(length_mm)
    if length <= 0:
        raise ValueError(f"Member overall length must be strictly positive, got {length:g} mm.")

    positions: list[float] = []
    for h in holes:
        if isinstance(h, (int, float)):
            positions.append(float(h))
        elif isinstance(h, Hole):
            positions.append(float(h.along_mm))
        elif isinstance(h, dict):
            pos = h.get("along_mm")
            if pos is not None:
                positions.append(float(pos))

    sorted_positions = sorted(set(positions))

    if sorted_positions:
        first_pos = sorted_positions[0]
        if first_pos <= 0:
            raise ValueError(
                f"Dimension chain violation: hole coordinate {first_pos:g} mm "
                f"must be strictly positive (0 < pos < {length:g} mm)."
            )
        last_pos = sorted_positions[-1]
        if last_pos >= length:
            raise ValueError(
                f"Dimension chain violation: last hole coordinate {last_pos:g} mm "
                f"must be strictly less than member overall length {length:g} mm."
            )

    level1_chain: list[DimensionItem] = []
    previous = 0.0
    for i, pos in enumerate(sorted_positions):
        delta = pos - previous
        kind = "edge" if i == 0 else "pitch"
        level1_chain.append(DimensionItem(
            start_mm=previous,
            end_mm=pos,
            value_mm=delta,
            label=f"{delta:g}",
            level=1,
            kind=kind,
        ))
        previous = pos

    if sorted_positions:
        end_delta = length - previous
        level1_chain.append(DimensionItem(
            start_mm=previous,
            end_mm=length,
            value_mm=end_delta,
            label=f"{end_delta:g}",
            level=1,
            kind="edge",
        ))

    level2_chain: list[DimensionItem] = [
        DimensionItem(
            start_mm=0.0,
            end_mm=length,
            value_mm=length,
            label=f"OVERALL = {length:g} mm",
            level=2,
            kind="overall",
        )
    ]

    chains = [level1_chain, level2_chain]
    if gauge_mm is not None:
        g_list = list(gauge_mm) if isinstance(gauge_mm, (list, tuple)) else [gauge_mm]
        valid_g = [float(g) for g in g_list if g is not None and float(g) > 0]
        if valid_g:
            level3_chain: list[DimensionItem] = []
            prev_g = 0.0
            for idx, g_val in enumerate(valid_g):
                delta_g = g_val - prev_g
                lbl = f"GAUGE = {g_val:g} mm" if len(valid_g) == 1 else f"GAUGE {idx+1} = {g_val:g} mm"
                level3_chain.append(DimensionItem(
                    start_mm=prev_g,
                    end_mm=g_val,
                    value_mm=delta_g,
                    label=lbl,
                    level=3,
                    kind="gauge",
                ))
                prev_g = g_val
            chains.append(level3_chain)

    return chains
