// Zone types the violation engine reads, how the dashboard shows them, and polygon helpers for the
// zone drawer (Configuration > Zones). The backend checks geometry again (routers/zones.py).

import type { Zone, ZoneFeature, ZoneType } from '../api/types';

export interface ZoneTypeInfo {
  label: string;
  color: string;
  /** the violations this zone type drives */
  drives: string;
  /** the setting the type takes */
  param: 'grace_s' | 'limit_kmh' | null;
}

export const ZONE_TYPES: Record<ZoneType, ZoneTypeInfo> = {
  no_parking: { label: 'No parking / no stopping', color: '#fa5252', drives: 'illegal stopping, no parking (short wait = no stopping)', param: 'grace_s' },
  crosswalk: { label: 'Zebra crossing', color: '#f8f9fa', drives: 'stopping on a zebra crossing, failing to yield to pedestrians', param: 'grace_s' },
  highway: { label: 'Highway (no stopping)', color: '#4c6ef5', drives: 'stopping on a highway', param: 'grace_s' },
  speed: { label: 'Speed limit', color: '#fab005', drives: 'speeding against the zone limit (E3)', param: 'limit_kmh' },
  no_u_turn: { label: 'No U-turn', color: '#be4bdb', drives: 'illegal U-turn', param: null },
};

export const zoneTypeOptions = (Object.keys(ZONE_TYPES) as ZoneType[]).map((t) => ({ value: t, label: ZONE_TYPES[t].label }));

export function zoneColor(type: string): string {
  return ZONE_TYPES[type as ZoneType]?.color ?? '#868e96';
}

/** The ring without its closing point. */
export function openRing(ring: [number, number][]): [number, number][] {
  const n = ring.length;
  return n > 1 && ring[0][0] === ring[n - 1][0] && ring[0][1] === ring[n - 1][1] ? ring.slice(0, -1) : ring;
}

/** An API / scene-file feature as the map's Zone. */
export function featureToZone(f: ZoneFeature): Zone {
  return { id: f.properties.id, type: String(f.properties.type), name: f.properties.name, source: f.properties.source, polygon: openRing(f.geometry.coordinates[0]) };
}

/** Shoelace area, m^2. */
export function polygonArea(pts: [number, number][]): number {
  let a = 0;
  for (let i = 0; i < pts.length; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[(i + 1) % pts.length];
    a += x0 * y1 - x1 * y0;
  }
  return Math.abs(a) / 2;
}

function cross(o: [number, number], a: [number, number], b: [number, number]) {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

function segmentsCross(p1: [number, number], p2: [number, number], q1: [number, number], q2: [number, number]) {
  const d1 = cross(q1, q2, p1), d2 = cross(q1, q2, p2), d3 = cross(p1, p2, q1), d4 = cross(p1, p2, q2);
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
}

/** Why the polygon can't be a zone (the backend's main checks), or null. */
export function polygonProblem(pts: [number, number][]): string | null {
  if (pts.length < 3) return 'needs at least 3 points';
  const n = pts.length;
  for (let i = 0; i < n; i++)
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue; // neighbouring edges share a point
      if (segmentsCross(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n])) return 'edges cross each other';
    }
  const a = polygonArea(pts);
  if (a < 1) return `area ${a.toFixed(1)} m² is under 1 m²`;
  if (a > 1_000_000) return 'area is over 1 km²';
  return null;
}
