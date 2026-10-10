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
  place_memory?: { radius_m?: number; keep_s?: number };
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
  | { type: 'event'; event: TrafficEvent };
