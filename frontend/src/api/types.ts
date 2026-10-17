// Types for the backend contract in backend/API.md and the schemas in schemas/.

export type Role = 'OFFICER' | 'OPERATOR' | 'PLANNER' | 'MAINTENANCE' | 'ADMIN';

export const VIOLATION_TYPES = [
  'no_parking',
  'wrong_way',
  'illegal_u_turn',
  'speeding',
  'lane_violation',
  'zebra_crossing',
  'highway_stop',
  'red_light',
] as const;
export type ViolationType = (typeof VIOLATION_TYPES)[number];
// not shown on the Violations page or as live notifications (2026-10-16: lane changes inside Town04's
// highway junctions read as A1 false alarms); the backend still stores them
export const HIDDEN_VIOLATION_TYPES: ReadonlySet<string> = new Set(['lane_violation']);
export const SHOWN_VIOLATION_TYPES = VIOLATION_TYPES.filter((t) => !HIDDEN_VIOLATION_TYPES.has(t));

export const ANOMALY_TYPES = ['pothole', 'crack', 'waterlogging', 'debris'] as const;
export type AnomalyType = (typeof ANOMALY_TYPES)[number];

export const VIOLATION_STATUSES = ['flagged', 'needs_review', 'suppressed', 'possible_breakdown'] as const;
export const ANOMALY_STATUSES = ['flagged', 'reviewed', 'work_order_issued', 'repaired'] as const;

export interface Review {
  outcome: 'confirmed' | 'dismissed';
  note?: string;
  by: string;
  at: string;
}

export interface Evidence {
  frame?: number;
  clip?: string;
  snapshot?: string;
  trajectory?: string;
  telemetry?: Record<string, unknown>;
  clip_url?: string;
  snapshot_url?: string;
  trajectory_url?: string;
  [k: string]: unknown;
}

interface EventBase {
  event_id: string;
  x: number;
  y: number;
  lane_id?: string | null;
  zone_id?: string | null;
  tags?: string[];
  confidence: number;
  status: string;
  evidence?: Evidence;
  session_id?: string;
  review?: Review | null;
}

export interface ViolationEvent extends EventBase {
  kind?: 'violation';
  type: ViolationType;
  condition?: string | null;
  track_ids: number[];
  cls: string;
  start_s: number;
  start_frame: number;
  flag_s: number;
  flag_frame: number;
  end_s?: number | null;
  end_frame?: number | null;
  value: Record<string, unknown>;
}

export interface AnomalyEvent extends EventBase {
  kind: 'anomaly';
  type: AnomalyType;
  t_s: number;
  frame: number;
  severity_score: number;
  severity_band?: 'low' | 'medium' | 'high';
  area_sq_m?: number;
  recurrence_count?: number;
}

export type TrafficEvent = ViolationEvent | AnomalyEvent;

export const isAnomaly = (e: TrafficEvent): e is AnomalyEvent => e.kind === 'anomaly';

/** Time the event became visible (violation: flag_s, anomaly: t_s). */
export const eventTime = (e: TrafficEvent): number => (isAnomaly(e) ? e.t_s : e.flag_s);

export interface Page<T> {
  total: number;
  items: T[];
}

export interface Session {
  session_id: string;
  name: string;
  source: 'carla' | 'video' | 'live';
  town?: string | null;
  flight?: string | null;
  started_at?: string | null;
  n_events: number;
  profile?: string | null;
  scene?: string | null;
}

/** GET /api/sessions/{id}/video: the overlay video as WebM; frame_t[i] = session time of frame i. */
/** annotated: the model's output (boxes, IDs, speeds); raw: the footage the model was given. */
export type VideoKind = 'raw' | 'annotated';

export interface SessionVideo {
  status: 'none' | 'encoding' | 'ready' | 'failed';
  progress?: number;
  error?: string;
  url?: string;
  fps?: number;
  frames?: number;
  frame_t?: number[];
  source?: string;
  kind?: VideoKind;
  /** status none: whether the backend has footage to prepare this video from */
  available?: boolean;
}

/** A live pipeline session that sent camera frames in the last 10 s (GET /api/live/sources). */
export interface LiveSource {
  session_id: string;
  kinds: VideoKind[];
  t_s: number | null;
  age_s: number;
}

export interface VehicleState {
  session_id: string;
  track_id: number;
  cls: string;
  x: number;
  y: number;
  speed_kmh: number;
  heading_deg: number;
  lane_id?: string | null;
  state: 'ok' | 'checking' | 'flagged';
  t_s: number;
}

/** `{track_id: [[t_s, x, y, speed_kmh], ...]}` */
export type Trajectories = Record<string, [number, number, number, number][]>;

/** Service statuses ("ok" or an error string) and the background clip transcodes. */
export interface Health {
  db?: string;
  redis?: string;
  s3?: string;
  clips?: { queued: number; done: number; skipped: number; failed: number; pending: number };
  [service: string]: string | Health['clips'] | undefined;
}

export interface Me {
  username: string;
  role: Role;
}

export interface LoginResponse {
  access_token: string;
  /** Single-use; swap it at POST /api/auth/refresh for a new pair. */
  refresh_token?: string;
  expires_in?: number;
  role: Role;
}

// Stats: the contract names the keys but not their exact shapes, so each is normalised in lib/stats.ts.
export interface Stats {
  by_type: unknown;
  by_condition: unknown;
  by_status: unknown;
  by_hour: unknown;
  hotspots: unknown;
}

export interface ViolationConfig {
  enabled?: boolean;
  params?: Record<string, unknown>;
}

export interface Profile {
  profile_version: 1;
  name: string;
  description?: string;
  applies_to?: {
    map?: string | null;
    site?: string | null;
    road_type?: 'urban' | 'highway' | 'mixed' | null;
    scenario?: string | null;
  };
  violations?: Partial<Record<ViolationType, ViolationConfig>>;
  conditions?: Record<string, boolean>;
  place_memory?: { radius_m?: number; keep_s?: number; vacate_kmh?: number; vacate_beyond_m?: number };
  model?: Record<string, unknown>;
  modules?: Record<string, boolean>;
  road?: Record<string, unknown>;
}

/** GET /api/profiles may return bare profiles or wrapped rows; lib/profiles.ts unwraps both. */
export interface ProfileVersion {
  version?: number;
  by?: string;
  who?: string;
  at?: string;
  when?: string;
  note?: string | null;
  profile?: Profile;
  [k: string]: unknown;
}

export interface Condition {
  id: string;
  group: string;
  name: string;
  engine_type: string | null;
  match?: Record<string, unknown>;
  status: 'built' | 'partial' | 'missing';
  priority?: number;
  alias_of?: string;
}

export interface ConditionsDoc {
  note?: string;
  types: Record<string, string[]>;
  conditions: Condition[];
}

export interface Lane {
  id: string;
  road_id: string;
  centreline: [number, number][];
  z?: number[];
  width_m?: number;
  lane_type?: string;
  junction?: boolean;
  left_line?: string;
  right_line?: string;
  lane_change?: string;
  speed_limit_kmh?: number | null;
  bridge?: boolean;
  tunnel?: boolean;
  next?: string[];
  road_class?: string;
  restricted?: string | null;
  one_way?: boolean;
  ramp?: string | null;
  median_left?: boolean;
  median_gap_m?: number;
  /** GET /api/scenes/{town}?profile=: the attributes that profile's road.lane_overrides changed */
  overridden?: string[];
}

/** A profile road.lane_overrides item (schemas/profile.schema.json): exact lane id or fnmatch glob
 * ("r46_*", "r46_s0_*"); later items win. */
export interface LaneOverride {
  lane_id: string;
  speed_limit_kmh?: number;
  restricted?: 'bus' | 'emergency' | 'restricted' | null;
  road_class?: 'urban' | 'highway';
  lane_type?: 'driving' | 'shoulder' | 'parking';
  lane_change?: 'none' | 'left' | 'right' | 'both';
  one_way?: boolean;
  bridge?: boolean;
  tunnel?: boolean;
  ramp?: 'on' | 'off' | 'link' | null;
  median_left?: boolean;
  note?: string;
}

/** GET /api/road/attributes: what a lane override may set and which conditions it drives. */
export interface RoadAttribute {
  key: string;
  label: string;
  type: 'enum' | 'bool' | 'number';
  values?: (string | null)[];
  drives: { condition: string; label: string }[];
  how?: string;
}

export interface Zone {
  id: string;
  type: string;
  polygon: [number, number][];
  source?: string;
  name?: string;
}

/** Zone types the violation engine reads (backend routers/zones.py TYPES). */
export type ZoneType = 'no_parking' | 'crosswalk' | 'highway' | 'speed' | 'no_u_turn';

/** GET /api/zones feature properties: an API zone (editable) or one from the scene / site file (read-only). */
export interface ZoneProps {
  id: string;
  scene: string;
  name?: string;
  type: ZoneType | string;
  grace_s?: number;
  limit_kmh?: number;
  active: boolean;
  version?: number;
  area_m2?: number;
  created_by?: string;
  created_at?: string;
  updated_by?: string;
  updated_at?: string;
  source: 'api' | 'scene_file' | string;
  editable?: boolean;
}

export interface ZoneFeature {
  type: 'Feature';
  id: string;
  geometry: { type: 'Polygon'; coordinates: [number, number][][] };
  properties: ZoneProps;
  /** create / update responses: the engine zone file the backend rewrote */
  file?: string | null;
}

export interface ZoneInput {
  scene?: string;
  name?: string;
  type?: string;
  polygon?: [number, number][];
  grace_s?: number | null;
  limit_kmh?: number | null;
  active?: boolean;
  note?: string;
}

export interface ZoneVersionRow {
  version: number;
  action: string;
  by: string;
  at: string;
  note: string | null;
  zone: ZoneFeature;
}

export interface Scene {
  scene: string;
  coords?: string;
  source?: string;
  note?: string;
  lanes: Lane[];
  zones?: Zone[];
  stop_lines?: unknown[];
  /** set when fetched with ?profile= */
  profile?: string;
  overrides_applied?: unknown;
}

/** Scene list entries: names or small objects, depending on the backend. */
export type SceneListEntry = string | { town?: string; scene?: string; name?: string; [k: string]: unknown };

/** Build Plan M8 recommendation (ml/planning/recommend.py: objects per field, Expected_Output 7.3);
 * older / mock shapes are plain text, so every field stays loosely typed and the page reads it defensively. */
export interface Recommendation {
  id: string;
  problem?: string;
  type?: string;
  area?: string;
  tier?: string;
  location?: unknown;
  evidence?: unknown;
  action?: unknown;
  expected_impact?: unknown;
  priority?: unknown;
  validation_method?: unknown;
  confidence?: unknown;
  simulation?: unknown;
  limitations?: string | string[];
  alternatives?: unknown;
  projected_estimate?: unknown;
  status?: string;
  decision?: string;
  [k: string]: unknown;
}

export interface PlannerHistoryEntry {
  id?: string | number;
  recommendation_id?: string;
  decision?: string;
  rationale?: string;
  by?: string;
  at?: string;
  validation?: unknown;
  [k: string]: unknown;
}

/** A generated export kept in the report history (GET /api/reports). */
export interface ReportRecord {
  id: string;
  format: 'pdf' | 'xlsx' | 'geojson' | 'csv';
  filename: string;
  filters: Record<string, string>;
  session_id: string | null;
  n_events: number;
  n_total: number;
  size_bytes: number;
  by: string;
  role: string;
  at: string;
  stored: boolean;
  download_url: string;
}

export type LiveMessage =
  | { type: 'vehicles'; items: VehicleState[] }
  | { type: 'event'; event: TrafficEvent; catch_up?: boolean }
  | { type: 'catch_up'; window_s: number; n: number; session_id: string | null };

// ---- traffic plans (scenario builder): backend/app/routers/plans.py, ml/planning/plans.py ----

export type PlanGroup = 'prop' | 'rule' | 'scenario';

export interface PlanItem {
  id: string;
  kind: string;
  x: number;
  y: number;
  yaw_deg?: number;
  params?: Record<string, unknown>;
  /** lane_id: the lane it acts on; junction_id (+ approach_id): a signal / stop sign on a junction's approach
   * (no approach_id = all of them); the virtual traffic runs one controller per junction */
  target?: { lane_id?: string; junction_id?: string; approach_id?: string };
  schedule?: { from_s?: number | null; to_s?: number | null };
  priority?: number;
  note?: string;
}

export interface PlanDoc {
  name: string;
  description: string;
  scene: string;
  session_id: string | null;
  objectives: string[];
  props: PlanItem[];
  rules: PlanItem[];
  scenarios: PlanItem[];
  /** custom traffic configuration (the Traffic tab); absent = defaults */
  traffic?: Record<string, unknown> | null;
}

export interface PlanParam {
  key: string;
  label: string;
  type: 'number' | 'enum' | 'bool' | 'text';
  default: unknown;
  min?: number;
  max?: number;
  unit?: string;
  options?: string[];
  help?: string;
}

export interface PlanKind {
  key: string;
  group: PlanGroup;
  label: string;
  target: 'point' | 'lane' | 'area';
  params: PlanParam[];
  replay: { mode: string; note: string };
  carla: { status: 'supported' | 'approximate' | 'unsupported'; how: string };
  virtual?: { status: 'supported' | 'approximate' | 'unsupported'; how: string };
}

export interface PlanTemplate {
  label: string;
  items: { group: PlanGroup; kind: string; params: Record<string, unknown> }[];
}

export interface PlanCatalogue {
  kinds: Record<string, PlanKind>;
  groups: PlanGroup[];
  templates: Record<string, PlanTemplate>;
  evidence_levels: string[];
}

export interface PlanMessage {
  level: 'error' | 'warning' | 'conflict' | 'info';
  item_id: string | null;
  code: string;
  message: string;
}

export interface PlanValidation {
  ok: boolean;
  errors: PlanMessage[];
  warnings: PlanMessage[];
  conflicts: PlanMessage[];
  infos: PlanMessage[];
}

/** One road and direction leading into a junction: its lanes, and the stop line across their ends (CARLA metres). */
export interface JunctionApproach {
  id: string;
  road_id: string | null;
  lanes: string[];
  x: number;
  y: number;
  heading_deg: number;
  stop_line: [number, number][];
}

/** A junction of the lane map (ml/violation_engine/junctions.py): outline, centre and approaches. */
export interface JunctionInfo {
  id: string;
  centre: [number, number];
  polygon: [number, number][];
  lanes?: string[];
  approaches: JunctionApproach[];
}

export interface PlanLocate {
  x: number;
  y: number;
  scene: string;
  on_road: boolean;
  inside_scene: boolean;
  nearest_lane: { id: string; road_id: number | string | null; distance_m: number; lane_type: string | null; speed_limit_kmh: number | null; junction: boolean; heading_deg: number } | null;
  lanes: { id: string; distance_m: number; lane_type: string | null; speed_limit_kmh: number | null; junction: boolean }[];
  junction: boolean;
  /** the junction at the position (inside its outline, or its centre within 25 m), from the scene's junctions; absent on older backends */
  junction_info?: JunctionInfo | null;
  zones: { id: string; type: string; distance_m: number }[];
  plan_items: { id: string; group: PlanGroup; kind: string; distance_m: number }[];
  signals: string;
}

export interface PlanAlternative {
  for_item: string;
  kind: string;
  title: string;
  benefit: string;
  risk: string;
  tradeoff: string;
  test: Record<string, unknown> | null;
  evidence: string;
  evidence_note: string;
}

export interface PlanReplayEvent { condition: string | null; type: string; track_ids: number[] | null; flag_s: number | null; x: number | null; y: number | null }

export interface PlanAnalysis {
  validation: PlanValidation;
  compiled: {
    evaluated: { item_id: string; kind: string; group: PlanGroup; what: string; note: string }[];
    not_evaluated: { item_id: string; kind: string; reason: string }[];
    changes: { lane_overrides: unknown[]; zones: unknown[] };
  };
  risks: { item_id: string; level: string; basis: string; message: string }[];
  alternatives: PlanAlternative[];
  pending_metrics: string[];
  evidence: { level: string; why: string };
  observed?: {
    label: string; centre: [number, number]; radius_m: number; duration_s: number; vehicles: number; samples: number;
    mean_speed_kmh: number | null; p85_speed_kmh: number | null; density_veh_per_km2: number | null; flow_veh_per_h: number | null;
    queue: { max_m: number; mean_m: number }; travel_time: { vehicles: number; mean_s: number | null; p85_s: number | null };
    conflicts_ttc: { count: number };
  };
  replay?: {
    label: string; note: string; scope: string; rows: number; tracks: number; seconds: number;
    baseline: { counted: number; by_type: Record<string, number> };
    modified: { counted: number; by_type: Record<string, number> };
    added: PlanReplayEvent[];
    removed: PlanReplayEvent[];
  };
  best_case?: { label: string; count: number; removed: PlanReplayEvent[]; note: string };
  worst_case?: { label: string; count: number; added: PlanReplayEvent[]; note: string };
}

export interface PlanRow {
  id: string;
  name: string;
  scene: string;
  owner: string;
  created_at: string;
  updated_at: string;
  latest_version: number;
  archived: boolean;
}

export interface PlanRun {
  id: string;
  plan_id: string | null;
  plan_version: number | null;
  session_id: string | null;
  kind: string;
  by: string;
  at: string;
  finished_at: string | null;
  status: 'running' | 'done' | 'failed';
  error: string | null;
  /** while running: the step it is on (the bar in the builder) */
  progress?: { stage: string; done: number; total: number } | null;
  plan?: PlanDoc;
  result?: (PlanAnalysis & Partial<VirtualResult>) | null;
  evidence?: string | null;
  replay_counted?: [number, number] | null;
}

export interface VirtualMetric {
  label: string;
  unit: string;
  better: number;
  baseline: { mean: number | null; ci95: number | null; n: number };
  plan: { mean: number | null; ci95: number | null; n: number };
  diff: number | null;
  verdict: string;
  direction: 'better' | 'worse' | 'neutral' | 'none';
}

/** POST /api/plans/simulate result: the virtual traffic simulator (ml/planning/microsim.py), not CARLA. */
export interface VirtualResult {
  label: string;
  seeds: number;
  duration_s: number;
  warmup_s: number;
  region: { centre: [number, number] | null; radius_m: number | null };
  notes: string[];
  signals?: VirtualSignal[];
  metrics: Record<string, VirtualMetric>;
  trajectories: { baseline: Record<string, [number, number, number, number | null][]>; plan: Record<string, [number, number, number, number | null][]> };
}

export interface PlanReportRow {
  id: string;
  plan_id: string | null;
  plan_version: number | null;
  title: string;
  format: 'pdf' | 'xlsx' | 'csv';
  filename: string;
  size_bytes: number;
  evidence: string | null;
  session_id: string | null;
  by: string;
  at: string;
  stored: boolean;
  download_url: string;
}

/** POST /api/plans/traffic: one virtual-traffic run for the map's "Custom traffic" switch. */
export interface TrafficResult {
  label: string;
  follows_plan: boolean;
  plan_items: number;
  seed: number;
  duration_s: number;
  vehicles: number;
  spawned?: Record<string, number>;
  signals?: VirtualSignal[];
  stops?: { made: number; prevented: number; in_parking_zones: number };
  config?: Record<string, unknown>;
  description?: string;
  stats?: { mean_speed_kmh: number | null; p85_speed_kmh: number | null; vehicles_in_region: number | null; density_veh_per_km2: number | null; throughput_veh_per_h: number | null; travel_time_s: number | null; queue_max_m: number | null; stopped_share: number | null; delay_s_per_vehicle: number | null };
  notes: string[];
  trajectories: Record<string, [number, number, number, number | null][]>;
}

export interface TrafficField {
  key: string;
  label: string;
  type: 'number' | 'enum' | 'bool';
  default: unknown;
  min?: number;
  max?: number;
  unit?: string;
  options?: string[];
  help?: string;
  step?: number;
}

/** GET /api/plans/traffic/schema */
export interface TrafficSchema {
  sections: { id: string; title: string; help: string; fields: TrafficField[] }[];
  defaults: Record<string, any>;
  presets: Record<string, { label: string; help: string; config: Record<string, any> }>;
  scenario_presets: Record<string, { label: string; help: string; traffic: Record<string, any>; items: { kind: string; params: Record<string, unknown>; schedule?: { from_s?: number | null; to_s?: number | null } }[] }>;
  profiles: Record<string, { label: string; points: [number, number][] }>;
  source_mixes: Record<string, Record<string, number>>;
  behaviours: Record<string, Record<string, number>>;
  vehicle_types: string[];
}

/** A virtual signal / stop sign / crossing / bus stop of a virtual-traffic run: stop-line heads and its phase over the run. */
export interface VirtualSignal {
  id: string;
  kind: 'traffic_signal' | 'stop_sign' | 'crossing' | 'bus_stop';
  mode: string;
  x: number;
  y: number;
  heads: { x: number; y: number; group: 'A' | 'B' }[];
  /** [seconds into the run, 'A' | 'B' | 'R' | 'ALL'] at each change: A / B = that group has green, R = all red, ALL = uncontrolled / free */
  timeline: [number, string][];
  pedestrian_groups?: number | null;
}
