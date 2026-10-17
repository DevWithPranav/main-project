// Synthetic data for mock mode (VITE_MOCK=1). Everything here is made up, drawn on the repo's real
// lane maps so the screens look like a real session. It is never shown as measured data: the UI
// carries a MOCK banner whenever this module is in use.

import conditionsDoc from '../../../schemas/conditions.json';
import type { AnomalyEvent, ConditionsDoc, Lane, Scene, Session, TrafficEvent, Trajectories, ViolationEvent } from '../api/types';

export const CONDITIONS = conditionsDoc as unknown as ConditionsDoc;

/** Small deterministic PRNG (mulberry32) so mock data is the same on every reload. */
export function rng(seed: number) {
  let a = seed >>> 0;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return {
    next,
    range: (lo: number, hi: number) => lo + (hi - lo) * next(),
    int: (lo: number, hi: number) => Math.floor(lo + (hi - lo + 1) * next()),
    pick: <T,>(xs: readonly T[]): T => xs[Math.floor(next() * xs.length)],
  };
}

/** A small synthetic grid town, used when the dev server cannot serve the repo lane maps. */
export function syntheticScene(name: string): Scene {
  const lanes: Lane[] = [];
  const n = 5;
  const step = 80;
  for (let i = 0; i < n; i++) {
    for (const dir of [1, -1]) {
      const off = dir * 1.75;
      const h: [number, number][] = [];
      const v: [number, number][] = [];
      for (let k = 0; k <= (n - 1) * step; k += 2) {
        const s = dir > 0 ? k : (n - 1) * step - k;
        h.push([s - 160, i * step - 160 + off]);
        v.push([i * step - 160 + off, s - 160]);
      }
      const base = { width_m: 3.5, lane_type: 'driving', junction: false, speed_limit_kmh: i === 2 ? 60 : 30 };
      lanes.push({ id: `h${i}_${dir}`, road_id: `h${i}`, centreline: h, ...base, road_class: i === 2 ? 'highway' : 'urban' });
      lanes.push({ id: `v${i}_${dir}`, road_id: `v${i}`, centreline: v, ...base, road_class: 'urban' });
    }
  }
  return { scene: name, coords: 'synthetic', note: 'synthetic mock grid', lanes, zones: [] };
}

function lerpAlong(pts: [number, number][], d: number): [number, number, number] | null {
  let acc = 0;
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1];
    const [x1, y1] = pts[i];
    const seg = Math.hypot(x1 - x0, y1 - y0);
    if (acc + seg >= d) {
      const f = seg > 0 ? (d - acc) / seg : 0;
      return [x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, (Math.atan2(y1 - y0, x1 - x0) * 180) / Math.PI];
    }
    acc += seg;
  }
  return null;
}

/** Follows lane successors to build a path of at least `minLen` metres. */
function lanePath(start: Lane, byId: Map<string, Lane>, minLen: number, r: ReturnType<typeof rng>) {
  const pts: [number, number][] = [...start.centreline];
  const laneAt: string[] = start.centreline.map(() => start.id);
  let lane = start;
  let len = 0;
  for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
  let guard = 0;
  while (len < minLen && guard++ < 20) {
    const nextId = lane.next && lane.next.length ? r.pick(lane.next) : null;
    const nxt = nextId ? byId.get(nextId) : undefined;
    if (!nxt) break;
    for (const p of nxt.centreline) {
      const last = pts[pts.length - 1];
      len += Math.hypot(p[0] - last[0], p[1] - last[1]);
      pts.push(p);
      laneAt.push(nxt.id);
    }
    lane = nxt;
  }
  return { pts, laneAt, len };
}

export interface MockSessionData {
  session: Session;
  events: (TrafficEvent & { _at: string })[];
  trajectories: Trajectories;
}

const IN_SCOPE = ['no_parking', 'wrong_way', 'illegal_u_turn', 'speeding', 'lane_violation', 'zebra_crossing', 'highway_stop'] as const;

function valueFor(type: string, r: ReturnType<typeof rng>, speed: number, limit: number): Record<string, unknown> {
  switch (type) {
    case 'speeding':
      return { speed_kmh: +Math.max(speed, limit * 1.25).toFixed(1), limit_kmh: limit, limit_source: r.pick(['lane', 'lane', 'zone', 'class']) };
    case 'wrong_way':
      return { angle_deg: +r.range(155, 180).toFixed(1), back_m: +r.range(5, 40).toFixed(1) };
    case 'illegal_u_turn':
      return { turn_deg: +r.range(160, 185).toFixed(1) };
    case 'lane_violation':
      return { overlap_m: +r.range(0.3, 1.4).toFixed(2), duration_s: +r.range(0.5, 6).toFixed(2) };
    default:
      return { dwell_s: +r.range(12, 90).toFixed(1), grace_s: type === 'no_parking' ? 30 : type === 'zebra_crossing' ? 10 : 20 };
  }
}

export function generateSession(opts: {
  id: string;
  name: string;
  source: Session['source'];
  scene: Scene;
  seed: number;
  startedAt: string;
  nTracks?: number;
  durationS?: number;
}): MockSessionData {
  const r = rng(opts.seed);
  const t0 = 1000;
  const dur = opts.durationS ?? 240;
  const drivable = opts.scene.lanes.filter((l) => (l.lane_type ?? 'driving') === 'driving' && !l.junction && l.centreline.length > 5);
  const byId = new Map(opts.scene.lanes.map((l) => [l.id, l]));
  const trajectories: Trajectories = {};
  const events: (TrafficEvent & { _at: string })[] = [];
  const started = Date.parse(opts.startedAt);
  const nTracks = opts.nTracks ?? 45;
  let evN = 0;

  // Keep vehicles in one neighbourhood so the map view is busy, like a drone's field of view.
  const centre = r.pick(drivable).centreline[0];
  const near = drivable.filter((l) => Math.hypot(l.centreline[0][0] - centre[0], l.centreline[0][1] - centre[1]) < 220);
  const pool = near.length > 10 ? near : drivable;

  for (let i = 0; i < nTracks && pool.length; i++) {
    const tid = 1000 + i * 7;
    const lane = r.pick(pool);
    const limit = lane.speed_limit_kmh ?? 30;
    const speed = r.range(limit * 0.6, limit * 1.4);
    const life = r.range(20, 70);
    const tStart = t0 + r.range(0, dur - 20);
    const { pts, laneAt } = lanePath(lane, byId, (speed / 3.6) * life + 5, r);
    const samples: [number, number, number, number][] = [];
    const sampleLane: string[] = [];
    for (let k = 0; ; k++) {
      const t = tStart + k * 0.2;
      if (t > tStart + life || t > t0 + dur) break;
      const d = (speed / 3.6) * k * 0.2;
      const p = lerpAlong(pts, d);
      if (!p) break;
      samples.push([+t.toFixed(2), +p[0].toFixed(2), +p[1].toFixed(2), +(speed + r.range(-1.5, 1.5)).toFixed(1)]);
      // nearest original vertex index for the lane id
      sampleLane.push(laneAt[Math.min(laneAt.length - 1, Math.round(d))] ?? lane.id);
    }
    if (samples.length < 10) continue;
    trajectories[String(tid)] = samples;

    if (r.next() < 0.4) {
      const type = r.pick(IN_SCOPE);
      const conds = CONDITIONS.conditions.filter((c) => c.engine_type === type);
      const cond = conds.length ? r.pick(conds) : null;
      const tag = cond?.match && typeof cond.match === 'object' && 'tag' in cond.match ? String(cond.match.tag) : null;
      const k = r.int(Math.floor(samples.length * 0.3), samples.length - 1);
      const [ft, fx, fy] = samples[k];
      const startS = Math.max(samples[0][0], ft - r.range(0.5, 8));
      const roll = r.next();
      const status = roll < 0.6 ? 'flagged' : roll < 0.85 ? 'needs_review' : roll < 0.95 ? 'suppressed' : 'possible_breakdown';
      evN += 1;
      const ev: ViolationEvent & { _at: string } = {
        kind: 'violation',
        event_id: `${opts.id}-${String(evN).padStart(4, '0')}`,
        type,
        condition: cond?.id ?? null,
        track_ids: [tid],
        cls: r.pick(['car', 'car', 'car', 'truck', 'bus', 'motorcycle']),
        start_s: +startS.toFixed(3),
        start_frame: Math.round((startS - t0) * 20),
        flag_s: ft,
        flag_frame: Math.round((ft - t0) * 20),
        end_s: +(ft + r.range(0.5, 10)).toFixed(3),
        end_frame: Math.round((ft + 5 - t0) * 20),
        lane_id: sampleLane[k],
        zone_id: type === 'zebra_crossing' ? `crosswalk_${r.int(0, 60)}` : `road_${byId.get(sampleLane[k])?.road_id ?? '?'}`,
        x: fx,
        y: fy,
        value: valueFor(type, r, speed, limit),
        tags: tag ? [tag] : r.next() < 0.2 ? ['queue'] : [],
        confidence: +r.range(0.55, 0.99).toFixed(3),
        status,
        evidence: { frame: Math.round((ft - t0) * 20) },
        session_id: opts.id,
        _at: new Date(started + (ft - t0) * 1000).toISOString(),
      };
      if (r.next() < 0.25) {
        ev.review = {
          outcome: r.next() < 0.75 ? 'confirmed' : 'dismissed',
          note: 'mock review',
          by: 'officer',
          at: new Date(started + (dur + 600) * 1000).toISOString(),
        };
      }
      events.push(ev);
    }
  }

  // A few road-surface anomalies on random lanes.
  const nAnom = r.int(3, 6);
  for (let i = 0; i < nAnom; i++) {
    const lane = r.pick(pool);
    const p = r.pick(lane.centreline);
    const t = t0 + r.range(0, dur);
    const score = +r.range(0.1, 0.95).toFixed(2);
    evN += 1;
    const a: AnomalyEvent & { _at: string } = {
      kind: 'anomaly',
      event_id: `${opts.id}-${String(evN).padStart(4, '0')}`,
      type: r.pick(['pothole', 'crack', 'waterlogging', 'debris'] as const),
      t_s: +t.toFixed(2),
      frame: Math.round((t - t0) * 20),
      x: p[0] + r.range(-1, 1),
      y: p[1] + r.range(-1, 1),
      lane_id: lane.id,
      severity_score: score,
      severity_band: score < 0.35 ? 'low' : score < 0.7 ? 'medium' : 'high',
      area_sq_m: +r.range(0.1, 3).toFixed(2),
      recurrence_count: r.int(1, 4),
      tags: [],
      confidence: +r.range(0.5, 0.95).toFixed(3),
      status: 'flagged',
      evidence: {},
      session_id: opts.id,
      _at: new Date(started + (t - t0) * 1000).toISOString(),
    };
    events.push(a);
  }

  events.sort((a, b) => a._at.localeCompare(b._at));
  return {
    session: {
      session_id: opts.id,
      name: opts.name,
      source: opts.source,
      town: opts.scene.scene,
      flight: opts.source === 'carla' ? opts.id : null,
      started_at: opts.startedAt,
      n_events: events.length,
      profile: opts.scene.scene.toLowerCase() === 'town05' ? 'town05' : 'default',
      scene: opts.scene.scene,
    },
    events,
    trajectories,
  };
}
