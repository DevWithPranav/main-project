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

export type Health = Record<string, string>;

export interface Me {
  username: string;
  role: Role;
}

export interface LoginResponse {
  access_token: string;
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
}

export interface Zone {
  id: string;
  type: string;
  polygon: [number, number][];
  source?: string;
}

export interface Scene {
  scene: string;
  coords?: string;
  source?: string;
  note?: string;
  lanes: Lane[];
  zones?: Zone[];
  stop_lines?: unknown[];
}

/** Scene list entries: names or small objects, depending on the backend. */
export type SceneListEntry = string | { town?: string; scene?: string; name?: string; [k: string]: unknown };

/** Build Plan M8 recommendation; every field optional since M8 is still being built. */
export interface Recommendation {
  id: string;
  problem?: string;
  type?: string;
  location?: unknown;
  evidence?: unknown;
  action?: string;
  expected_impact?: unknown;
  priority?: string | number;
  validation_method?: string;
  confidence?: number | string;
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

export type LiveMessage =
  | { type: 'vehicles'; items: VehicleState[] }
  | { type: 'event'; event: TrafficEvent };
