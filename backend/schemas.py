"""Pydantic data schemas for API requests, responses, and real engine models."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PipelineStage(str, Enum):
    UPLOAD = "UPLOAD"
    DXF_PARSE = "DXF_PARSE"
    MEMBER_EXTRACTION = "MEMBER_EXTRACTION"
    LOCATOR_EXTRACTION = "LOCATOR_EXTRACTION"
    CALLOUT_EXTRACTION = "CALLOUT_EXTRACTION"
    GEOMETRY = "GEOMETRY"
    TOPOLOGY = "TOPOLOGY"
    INFERENCE = "INFERENCE"
    SHOP_DRAWINGS = "SHOP_DRAWINGS"
    BOM = "BOM"
    VALIDATION = "VALIDATION"
    COMPLETE = "COMPLETE"


class Job(BaseModel):
    id: str
    name: str
    source_dxf: str
    created_at: str
    status: JobStatus = JobStatus.CREATED
    current_stage: PipelineStage = PipelineStage.UPLOAD
    progress: int = 0
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    output_dir: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class JobProgressEvent(BaseModel):
    job_id: str
    stage: str
    progress: int
    message: str
    elapsed_seconds: float = 0.0
    completed_stages: list[str] = Field(default_factory=list)


class PipelineMetrics(BaseModel):
    members_total: int = 0
    locators_count: int = 0
    callouts_count: int = 0
    callout_groups: int = 0
    joints_count: int = 0
    topology_groups: int = 0
    connection_candidates: int = 0
    auto_allocated: int = 0
    review_count: int = 0
    reject_count: int = 0
    blocked_count: int = 0
    buildable_count: int = 0
    generated_drawings: int = 0
    bom_rows: int = 0
    golden_gate_status: str = "N/A"
    reference_drawings_passed: int = 0


class MemberItem(BaseModel):
    backmark: str
    section: str
    canonical_section: Optional[str] = None
    section_family: Optional[str] = None
    length_mm: float
    quantity: Optional[int] = None
    geometry_status: str = "EXTRACTED"
    topology_status: str = "UNMAPPED"
    inference_status: str = "REVIEW"
    drawing_status: str = "BLOCKED"
    bom_status: str = "PENDING"
    reasons: list[str] = Field(default_factory=list)


class LocatorItem(BaseModel):
    member_backmark: str
    source_entity: str = "MEMBER_LOCATOR"
    x: float = 0.0
    y: float = 0.0
    confidence: float = 1.0
    associated_member: Optional[str] = None
    association_status: str = "LOCATED"
    section: Optional[str] = None
    length_mm: Optional[float] = None
    inferred: bool = False
    inferred_from: Optional[str] = None
    image_url: Optional[str] = None
    has_crop: bool = False
    raw_text: Optional[str] = None
    bolt_count: Optional[int] = None
    diameter_mm: Optional[float] = None
    group_id: Optional[str] = None


class LocatorsSummary(BaseModel):
    assembly_image_url: Optional[str] = None
    total_locators: int = 0
    total_members: int = 0
    total_callouts: int = 0
    items: list[LocatorItem] = Field(default_factory=list)



class TopologyJoint(BaseModel):
    joint_id: str
    x: float = 0.0
    y: float = 0.0
    connected_members: list[str] = Field(default_factory=list)
    callout_groups: list[str] = Field(default_factory=list)


class CandidateItem(BaseModel):
    member: str
    end: str
    group_id: str
    callout_text: str = ""
    bolt_type: str = ""
    bolt_count: int = 0
    score: float = 0.0
    confidence: float = 0.0
    status: str = "REVIEW"
    evidence: dict[str, Any] = Field(default_factory=dict)
    leader_distance: Optional[float] = None
    joint_distance: Optional[float] = None
    competing_owner: Optional[str] = None


class ShopDrawingItem(BaseModel):
    drawing_id: str
    backmark: str
    section: str
    length_mm: float
    quantity: int
    status: str
    scale: str = "1:10"
    hole_count: int = 0
    has_pdf: bool = False
    has_dxf: bool = False
    has_json: bool = False
    pdf_url: Optional[str] = None
    dxf_url: Optional[str] = None
    json_url: Optional[str] = None


class BOMItem(BaseModel):
    backmark: str
    section: str
    quantity: int
    length_mm: float
    weight_kg: float = 0.0
    bolt_type: str = "-"
    bolt_count: int = 0
    hole_diameter_mm: float = 0.0
    status: str = "VALIDATED"


class ComparisonRow(BaseModel):
    property: str
    actual_reference: str
    generated_cad: str
    status: str
    variance: Optional[str] = None
    is_match: bool = True


class RegressionDrawingScore(BaseModel):
    drawing_id: str
    backmark: str
    metadata_score: float
    fabrication_score: float
    geometry_score: float
    dimension_score: float
    layout_score: float
    visual_score: float
    overall_score: float
    status: str
    missing_fields: list[str] = Field(default_factory=list)
    provenance_issues: list[str] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)
    preview_url: Optional[str] = None
    reference_url: Optional[str] = None
    diff_url: Optional[str] = None
    comparison_table: list[ComparisonRow] = Field(default_factory=list)


class ReviewItem(BaseModel):
    backmark: str
    drawing_id: str
    status: str
    severity: str = "BLOCKING"
    reason: str
    evidence: str = ""
    source: str = "MEMBER_SCHEDULE"
    confidence: float = 1.0
    recommended_action: str = ""
