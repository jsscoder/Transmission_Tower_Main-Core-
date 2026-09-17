export type JobStatus = 'CREATED' | 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED';

export type PipelineStage =
  | 'UPLOAD'
  | 'DXF_PARSE'
  | 'MEMBER_EXTRACTION'
  | 'LOCATOR_EXTRACTION'
  | 'CALLOUT_EXTRACTION'
  | 'GEOMETRY'
  | 'TOPOLOGY'
  | 'INFERENCE'
  | 'SHOP_DRAWINGS'
  | 'BOM'
  | 'VALIDATION'
  | 'COMPLETE';

export interface Job {
  id: string;
  name: string;
  source_dxf: string;
  created_at: string;
  status: JobStatus;
  current_stage: PipelineStage;
  progress: number;
  started_at?: string;
  completed_at?: string;
  error?: string;
  output_dir: string;
  parameters: Record<string, any>;
}

export interface JobProgressEvent {
  job_id: string;
  stage: string;
  progress: number;
  message: string;
  elapsed_seconds: number;
  completed_stages: string[];
}

export interface PipelineMetrics {
  members_total: number;
  locators_count: number;
  callouts_count: number;
  callout_groups: number;
  joints_count: number;
  topology_groups: number;
  connection_candidates: number;
  auto_allocated: number;
  review_count: number;
  reject_count: number;
  blocked_count: number;
  buildable_count: number;
  generated_drawings: number;
  bom_rows: number;
  golden_gate_status: string;
  reference_drawings_passed: number;
}

export interface MemberItem {
  backmark: string;
  section: string;
  canonical_section?: string;
  section_family?: string;
  length_mm: number;
  quantity?: number;
  geometry_status: string;
  topology_status: string;
  inference_status: string;
  drawing_status: string;
  bom_status: string;
  reasons: string[];
}

export interface LocatorItem {
  member_backmark: string;
  source_entity: string;
  x: number;
  y: number;
  confidence: number;
  associated_member?: string;
  association_status: string;
  section?: string;
  length_mm?: number;
  inferred?: boolean;
  inferred_from?: string;
  image_url?: string;
  has_crop?: boolean;
  raw_text?: string;
  bolt_count?: number;
  diameter_mm?: number;
  group_id?: string;
}

export interface LocatorsSummary {
  assembly_image_url: string | null;
  total_locators: number;
  total_members: number;
  total_callouts: number;
  items: LocatorItem[];
}

export interface TopologyJoint {
  joint_id: string;
  x: number;
  y: number;
  connected_members: string[];
  callout_groups: string[];
}

export interface CandidateItem {
  member: string;
  end: string;
  group_id: string;
  callout_text: string;
  bolt_type: string;
  bolt_count: number;
  score: number;
  confidence: number;
  status: string;
  evidence: Record<string, any>;
  leader_distance?: number;
  joint_distance?: number;
  competing_owner?: string;
}

export interface ShopDrawingItem {
  drawing_id: string;
  backmark: string;
  section: string;
  length_mm: number;
  quantity: number;
  status: string;
  scale: string;
  hole_count: number;
  has_pdf: boolean;
  has_dxf: boolean;
  has_json: boolean;
  pdf_url?: string;
  dxf_url?: string;
  json_url?: string;
}

export interface BOMItem {
  backmark: string;
  section: string;
  quantity: number;
  length_mm: number;
  weight_kg: number;
  bolt_type: string;
  bolt_count: number;
  hole_diameter_mm: number;
  status: string;
}

export interface ComparisonRow {
  property: string;
  actual_reference: string;
  generated_cad: string;
  status: string;
  variance?: string;
  is_match: boolean;
}

export interface RegressionDrawingScore {
  drawing_id: string;
  backmark: string;
  metadata_score: number;
  fabrication_score: number;
  geometry_score: number;
  dimension_score: number;
  layout_score: number;
  visual_score: number;
  overall_score: number;
  status: string;
  missing_fields: string[];
  provenance_issues: string[];
  differences: string[];
  preview_url?: string;
  reference_url?: string;
  diff_url?: string;
  comparison_table?: ComparisonRow[];
}

export interface ReviewItem {
  backmark: string;
  drawing_id: string;
  status: string;
  severity: string;
  reason: string;
  evidence: string;
  source: string;
  confidence: number;
  recommended_action: string;
}
