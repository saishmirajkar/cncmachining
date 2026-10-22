export type Severity = "error" | "warning" | "info";

export interface Membership {
  organization_id: string;
  organization_name: string;
  role: string;
}
export interface Me {
  id: string;
  email: string;
  full_name: string;
  is_platform_admin: boolean;
  memberships: Membership[];
}

export interface Project {
  id: string;
  name: string;
  description: string;
  customer_reference: string;
  part_number: string;
  archived: boolean;
  created_at: string;
  design_count: number;
  job_count: number;
}

export interface Feature {
  id: string;
  type: string;
  subtype: string;
  geometry: Record<string, any>;
  measurements: Record<string, any>;
  access_directions: string[];
  confidence: number;
  source: string;
  limitations: string[];
  face_refs: { surface: string; centroid: number[]; area: number; role?: string }[];
  counterbore?: { diameter: number; depth: number } | null;
  chamfer?: Record<string, number> | null;
  bottom?: { type: string; included_angle_deg?: number } | null;
  thread_hints?: { kind: string; thread: string; message: string }[];
  parent_feature_id?: string | null;
}

export interface Finding {
  code: string;
  severity: Severity;
  message: string;
  layer?: string;
  lines?: number[];
  detail?: Record<string, any>;
}

export interface Analysis {
  id: string;
  status: string;
  engine: string;
  engine_version: string;
  representation: string;
  unit_scale_to_mm: number;
  summary: Record<string, any>;
  features: Feature[];
  warnings: Finding[];
  limitations: string[];
  error: string;
  duration_ms: number;
  created_at: string;
}

export interface Design {
  id: string;
  project_id: string;
  revision: number;
  original_filename: string;
  file_format: string;
  size_bytes: number;
  sha256: string;
  detected_units: string | null;
  confirmed_units: string | null;
  status: string;
  status_message: string;
  created_at: string;
  has_mesh: boolean;
  latest_analysis: Analysis | null;
}

export interface Mesh {
  positions: number[];
  normals: number[];
  indices: number[];
  face_ranges: { face: number; start: number; count: number }[];
  segments?: number[];
}

export interface PostprocessorSummary {
  id: string;
  key: string;
  name: string;
  version: string;
  status: string;
  config_sha256: string;
  controller_family: string;
  file_extension: string;
  dialect: string;
  known_limitations: string[];
  documentation_refs: string[];
  license: string;
  created_at: string;
  config?: Record<string, any>;
  verifications?: {
    id: string;
    level: string;
    method: string;
    result: string;
    evidence: Record<string, any>;
    reviewer_name: string;
    notes: string;
    verified_at: string;
  }[];
}

export interface MachineModel {
  id: string;
  manufacturer: { id: string; name: string; country: string };
  family: string;
  model: string;
  category: string;
  axis_count: number;
  axes: { name: string; type: string; direction?: string; min?: number; max?: number }[];
  travel_x_mm: number | null;
  travel_y_mm: number | null;
  travel_z_mm: number | null;
  spindle_min_rpm: number | null;
  spindle_max_rpm: number | null;
  spindle_power_kw: number | null;
  spindle_taper: string;
  max_feed_mm_min: number | null;
  rapid_rate_mm_min: number | null;
  tool_changer: Record<string, any>;
  coolant_options: string[];
  workholding_options: string[];
  is_example: boolean;
  source: string;
  verification_status: string;
  notes: string;
}

export interface MachineConfig {
  id: string;
  name: string;
  scope: "global" | "organization";
  machine_model: MachineModel;
  controller: { manufacturer: string; model: string; family: string; version: string; version_id: string; model_id: string; version_notes: string };
  postprocessor: PostprocessorSummary | null;
  verification_status: string;
  source_type: string;
  source: string;
  is_active: boolean;
  is_example: boolean;
  supported_operations: string[];
  supported_file_extensions: string[];
  known_limitations: string[];
  verified_by: string;
  verified_at: string | null;
  effective_limits: Record<string, any>;
  machine_blocks?: Record<string, any>;
  overrides?: Record<string, any>;
  tool_change_time_s?: number | null;
  configuration_notes?: string;
}

export interface Tool {
  id: string;
  library_id: string;
  tool_number: number;
  name: string;
  tool_type: string;
  diameter_mm: number;
  cutting_length_mm: number;
  overall_length_mm: number;
  shank_diameter_mm: number | null;
  flute_count: number;
  corner_radius_mm: number;
  tip_angle_deg: number | null;
  tool_material: string;
  coating: string;
  manufacturer: string;
  product_code: string;
  length_offset_number: number | null;
  radius_offset_number: number | null;
  condition: string;
  limitations: string;
  cutting_data: Record<string, any>;
  active: boolean;
  assembly: {
    holder_name: string;
    holder_diameter_mm: number;
    holder_length_mm: number;
    stick_out_mm: number;
    measured_length_offset_mm: number | null;
    notes: string;
  } | null;
}

export interface ToolLibrary {
  id: string;
  name: string;
  description: string;
  machine_configuration_id: string | null;
  tools: Tool[];
}

export interface MaterialGrade {
  id: string;
  designation: string;
  standard: string;
  equivalents: string[];
  condition: string;
  hardness: string;
  cutting_data_source: string;
  cutting_data_confidence: string;
  material: { id: string; name: string; family: string; iso_group: string };
}
export interface Material {
  id: string;
  name: string;
  family: string;
  iso_group: string;
  grades: MaterialGrade[];
}

export interface Requirement {
  field: string;
  severity: Severity;
  message: string;
}

export interface Operation {
  id: string;
  sequence: number;
  name: string;
  op_type: string;
  enabled: boolean;
  target_feature_ids: string[];
  tool_id: string | null;
  strategy: string;
  cut_direction: string;
  stepdown_mm: number | null;
  stepover_percent: number | null;
  spindle_rpm: number | null;
  feed_mm_min: number | null;
  plunge_feed_mm_min: number | null;
  start_depth_mm: number | null;
  final_depth_mm: number | null;
  finish_allowance_mm: number;
  clearance_height_mm: number | null;
  safe_height_mm: number | null;
  entry_strategy: string;
  exit_strategy: string;
  coolant: string;
  tolerance_mm: number | null;
  parameters: Record<string, any>;
  parameter_sources: Record<string, any>;
  estimated_duration_s: number | null;
  validation_status: string;
  issues: string[];
  unresolved_decisions: unknown[];
  user_modified: boolean;
}

export interface Decision {
  code: string;
  message: string;
  feature_ids: string[];
}

export interface Setup {
  id: string;
  project_id: string;
  design_id: string;
  name: string;
  orientation: string | null;
  rot_z_deg: number;
  work_offset: string | null;
  datum: string | null;
  work_offset_machine_position: Record<string, number> | null;
  clearance_height_mm: number | null;
  safe_height_mm: number | null;
  coolant: string | null;
  tolerance_mm: number | null;
  surface_finish_ra_um: number | null;
  max_stepdown_mm: number | null;
  strategy_preference: string;
  cut_direction: string;
  finish_pass: boolean;
  inspection_requirements: string;
  production_quantity: number | null;
  priorities: string;
  status: string;
  plan_approved: boolean;
  plan_report: { issues?: Finding[]; unresolved?: Decision[]; planned_at?: string };
  machine_configuration_id: string | null;
  tool_library_id: string | null;
  requirements: Requirement[];
  ready_for_planning: boolean;
  geometry: {
    origin_oriented?: number[];
    wcs_matrix?: number[][];
    stock_wcs?: { min: number[]; max: number[] };
    part_wcs?: { min: number[]; max: number[] };
    clearance_z_wcs?: number;
    safe_z_wcs?: number;
    keep_out_boxes?: { name: string; min: number[]; max: number[] }[];
    workholding_info?: Record<string, any>;
  };
  analysis_id: string | null;
  datums: Record<string, string>;
  stock: {
    id: string;
    shape: string;
    dimensions: { x: number; y: number; z: number };
    part_offset: { x: number; y: number; z: number };
    finishing_allowance_mm: number;
    material_grade_id: string | null;
    material_condition: string;
    hardness: string;
  } | null;
  workholding: {
    id: string;
    kind: string;
    name: string;
    parameters: Record<string, any>;
    keep_out_boxes: unknown[];
    clamping_restrictions: string;
  } | null;
  machine_configuration: MachineConfig | null;
  operations: Operation[];
  material_grade: { id: string; designation: string } | null;
}

export interface CamJob {
  id: string;
  project_id: string;
  project_name: string;
  setup_id: string;
  design_id: string;
  status: string;
  engine: string;
  engine_version: string;
  progress: number;
  progress_message: string;
  error: string;
  attempts: number;
  cancel_requested: boolean;
  snapshot_sha256: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  machine: string;
  postprocessor: { key: string; version: string; status: string };
  program_ids: string[];
  design_filename: string;
  operation_results?: { operation_id: string; name: string; status: string; messages: string[]; command_count: number; move_count?: number }[];
  log?: { at: string; message: string }[];
  input_snapshot?: Record<string, any>;
  artifacts?: { id: string; format: string; sha256: string; stats: Record<string, any> }[];
}

export interface Gate {
  code: string;
  passed: boolean;
  message: string;
}

export interface ValidationReport {
  id: string;
  status: string;
  checks: Finding[];
  summary: Record<string, any>;
  verification_levels: Record<string, string>;
  engine_versions: Record<string, string>;
  created_at: string;
  simulation: { engine: string; resolution_mm: number; stats: Record<string, any>; limitations: string[] } | null;
}

export interface Revision {
  id: string;
  program_id: string;
  revision_number: number;
  parent_revision_id: string | null;
  source: string;
  sha256: string;
  file_extension: string;
  line_count: number;
  operation_map: { operation_id: string; name: string; tool_number: number; start_line: number; end_line: number }[];
  post_warnings: Finding[];
  change_note: string;
  status: string;
  released: boolean;
  released_at: string | null;
  created_at: string;
  release_gates: Gate[];
  validation: ValidationReport | null;
  review: { decision: string; acknowledgements: string[]; comments: string; reviewer_id: string; created_at: string } | null;
  prove_outs: { result: string; machine_serial: string; notes: string; created_at: string }[];
  required_acknowledgements: string[];
  content?: string;
}

export interface Program {
  id: string;
  project_id: string;
  cam_job_id: string;
  program_name: string;
  status: string;
  created_at: string;
  released_revision_id: string | null;
  postprocessor: { id: string; key: string; version: string; status: string; config_sha256: string };
  machine_configuration: { id: string; name: string; verification_status: string };
  design_filename: string;
  revisions: Revision[];
}

export interface Simulation {
  engine: string;
  resolution_mm: number;
  stats: Record<string, any>;
  limitations: string[];
  heightmap: { x0: number; y0: number; dx: number; nx: number; ny: number; z: (number | null)[]; stock_bottom: number } | null;
  backplot: { positions: number[]; kinds: number[]; lines: number[]; tools: number[]; ops: number[] } | null;
  stock_wcs: { min: number[]; max: number[] };
  part_wcs: { min: number[]; max: number[] };
  wcs_matrix: number[][];
  keep_out_boxes: { name: string; min: number[]; max: number[] }[];
  operations: Revision["operation_map"];
  design_id: string;
  tools: { number: number; name: string; diameter: number; type: string }[];
}
