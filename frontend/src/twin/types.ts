/** Shapes the twin reads: the lane map (ml/violation_engine/lane_map.py docstring, schemas/scene.schema.json)
 * and the backend contract (backend/API.md). Only the fields the twin uses are typed. */

export interface Lane {
  id: string;
  road_id?: string | null;
  centreline: [number, number][];
  z?: number[] | null;
  width_m?: number;
  lane_type?: string; // driving / shoulder / parking / ...
  junction?: boolean;
  left_line?: string;
  right_line?: string;
  lane_change?: string;
  speed_limit_kmh?: number | null;
  bridge?: boolean;
  tunnel?: boolean;
  ramp?: string | null;
  road_class?: string | null;
  one_way?: boolean | null;
  median_left?: boolean;
  median_gap_m?: number | null;
  restricted?: string | null;
  next?: string[];
}

export interface Zone {
  id: string;
  type: string; // crosswalk / no_parking / no_u_turn / highway / speed
  polygon: [number, number][];
  [k: string]: unknown;
}

export interface Scene {
  scene: string;
  coords?: string;
  lanes: Lane[];
  zones?: Zone[];
  stop_lines?: unknown[];
}

export type VehicleStateName = "ok" | "checking" | "flagged";

export interface VehicleState {
  session_id?: string;
  track_id: number | string;
  cls?: string;
  x: number;
  y: number;
  speed_kmh?: number | null;
  heading_deg?: number | null;
  lane_id?: string | null;
  state?: VehicleStateName;
  t_s?: number;
}

export interface TwinEvent {
  event_id: string;
  kind?: "violation" | "anomaly";
  type: string;
  condition?: string | null;
  track_ids?: number[];
  cls?: string;
  start_s?: number;
  flag_s?: number;
  end_s?: number | null;
  t_s?: number; // anomalies
  severity_score?: number; // anomalies (schemas/event.schema.json)
  severity_band?: "low" | "medium" | "high";
  area_sq_m?: number;
  lane_id?: string | null;
  zone_id?: string | null;
  x: number;
  y: number;
  confidence?: number;
  status?: string;
  value?: Record<string, unknown>;
  session_id?: string;
}

export interface Session {
  session_id: string;
  name?: string;
  source?: string;
  town?: string;
  flight?: string;
  started_at?: string;
  n_events?: number;
  profile?: string | null;
  scene?: string | null;
}

/** GET /api/sessions/{id}/trajectories: {track_id: [[t_s, x, y, speed_kmh], ...]} */
export type Trajectories = Record<string, [number, number, number, number | null][]>;

export interface LaneOverride {
  lane_id: string;
  speed_limit_kmh?: number;
  restricted?: "bus" | "emergency" | "restricted" | null;
}

/** export_town_objects.py: c = box centre, e = half extents (CARLA metres), r = [pitch, yaw, roll] deg. */
export interface TownObjects {
  scene: string;
  labels: string[];
  counts?: Record<string, number>;
  objects: { l: number; c: [number, number, number]; e: [number, number, number]; r: [number, number, number] }[];
}
