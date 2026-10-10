/**
 * Replay of a recorded session (pure; unit tested): trajectories from
 * GET /api/sessions/{id}/trajectories ({track_id: [[t_s, x, y, speed_kmh], ...]}) sampled at any
 * t_s, with each vehicle's state derived from the session's events:
 *   flagged  (red)   from flag_s to end_s (+ FLAG_HOLD_S so short events stay visible)
 *   checking (amber) from start_s to flag_s (a rule is timing the vehicle)
 *   ok       (green) otherwise
 * Times are the session's own clock (CARLA sim time for recorded flights), the same t_s the live
 * states and events carry, so a ?t= / postMessage seek from the dashboard video lines up.
 */
import type { TwinEvent, Trajectories, VehicleState, VehicleStateName } from "./types";

export const FLAG_HOLD_S = 3.0; // keep a vehicle red this long after its event ends (display only)
export const HOLD_S = 0.6; // show a track this long past its last sample (5 Hz data has 0.2 s gaps)

export interface Track {
  id: string;
  t: number[];
  x: number[];
  y: number[];
  v: (number | null)[];
}

export function parseTrajectories(tr: Trajectories): Track[] {
  const out: Track[] = [];
  for (const [id, rows] of Object.entries(tr || {})) {
    const r = (rows || []).filter((p) => Array.isArray(p) && p.length >= 3 && [p[0], p[1], p[2]].every(Number.isFinite));
    if (!r.length) continue;
    r.sort((a, b) => a[0] - b[0]);
    out.push({ id, t: r.map((p) => p[0]), x: r.map((p) => p[1]), y: r.map((p) => p[2]), v: r.map((p) => (p[3] == null ? null : p[3])) });
  }
  return out;
}

export function timeRange(tracks: Track[]): [number, number] {
  let a = Infinity, b = -Infinity;
  for (const t of tracks) {
    a = Math.min(a, t.t[0]);
    b = Math.max(b, t.t[t.t.length - 1]);
  }
  return Number.isFinite(a) ? [a, b] : [0, 0];
}

/** Index of the last sample with time <= t (-1 if none). */
function lastAtOrBefore(ts: number[], t: number): number {
  let lo = 0, hi = ts.length - 1, ans = -1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (ts[m] <= t) {
      ans = m;
      lo = m + 1;
    } else hi = m - 1;
  }
  return ans;
}

export interface Sample {
  x: number;
  y: number;
  speed_kmh: number | null;
  heading_deg: number | null;
}

/** Position at time t (linear interpolation), or null when the track isn't visible at t. */
export function sampleTrack(tr: Track, t: number, hold = HOLD_S): Sample | null {
  const n = tr.t.length;
  if (t < tr.t[0] || t > tr.t[n - 1] + hold) return null;
  const i = lastAtOrBefore(tr.t, t);
  const j = Math.min(i + 1, n - 1);
  const a = Math.max(i, 0);
  const span = tr.t[j] - tr.t[a];
  const f = j > a && span > 0 ? Math.min(1, (t - tr.t[a]) / span) : 0;
  const x = tr.x[a] + (tr.x[j] - tr.x[a]) * f;
  const y = tr.y[a] + (tr.y[j] - tr.y[a]) * f;
  // heading from the surrounding samples; speed given, else from displacement
  const p = Math.max(0, a - (j > a ? 0 : 1)), q = j > a ? j : a;
  const dx = tr.x[q] - tr.x[p], dy = tr.y[q] - tr.y[p], dt = tr.t[q] - tr.t[p];
  const moved = Math.hypot(dx, dy);
  const heading = moved > 0.05 ? (Math.atan2(dy, dx) * 180) / Math.PI : null;
  let speed = tr.v[a];
  if (speed == null && dt > 0) speed = (moved / dt) * 3.6;
  return { x, y, speed_kmh: speed ?? null, heading_deg: heading };
}

export type EventIndex = Map<string, TwinEvent[]>;

export function indexEventsByTrack(events: TwinEvent[]): EventIndex {
  const m: EventIndex = new Map();
  for (const e of events) for (const id of e.track_ids || []) {
    const k = String(id);
    if (!m.has(k)) m.set(k, []);
    m.get(k)!.push(e);
  }
  return m;
}

export function stateAt(trackId: string, t: number, idx: EventIndex): VehicleStateName {
  let st: VehicleStateName = "ok";
  for (const e of idx.get(trackId) || []) {
    const flag = e.flag_s ?? e.start_s;
    if (flag == null) continue;
    const end = Math.max(e.end_s ?? flag, flag) + FLAG_HOLD_S;
    if (t >= flag && t <= end) return "flagged";
    if (e.start_s != null && t >= e.start_s && t < flag) st = "checking";
  }
  return st;
}

/** All visible vehicles at t, in the live-state shape (backend/API.md Vehicle state). */
export function statesAt(tracks: Track[], t: number, idx: EventIndex): VehicleState[] {
  const out: VehicleState[] = [];
  for (const tr of tracks) {
    const s = sampleTrack(tr, t);
    if (!s) continue;
    out.push({ track_id: tr.id, x: s.x, y: s.y, speed_kmh: s.speed_kmh, heading_deg: s.heading_deg, state: stateAt(tr.id, t, idx), t_s: t });
  }
  return out;
}

/** Event time used for "show pins up to t". */
export function eventTime(e: TwinEvent): number {
  return e.flag_s ?? e.start_s ?? e.t_s ?? 0;
}
