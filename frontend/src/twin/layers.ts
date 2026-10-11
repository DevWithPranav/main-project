/**
 * Twin overlay layers (pure; unit tested): event pin styles (violations vs road-surface anomalies),
 * hotspots, problem road sections and live-vehicle trails.
 *
 * Hotspots use the backend's definition (backend/app/routers/events.py, GET /api/stats): events
 * binned on a 25 m grid, top 10 cells, centre = mean event position. They are computed here from the
 * session's events so live events show up without another request.
 */
import type { TwinEvent, VehicleState } from "./types";

export const HOTSPOT_CELL_M = 25; // same grid as GET /api/stats hotspots
export const HOTSPOT_TOP = 10;
export const TRAIL_S = 3; // trail length, seconds (dashboard uses the same)

/** Dashboard colours (frontend/src/lib/format.ts TYPE_COLORS, Mantine 6 shades). */
export const TYPE_COLOR: Record<string, string> = {
  no_parking: "#7950f2", wrong_way: "#fa5252", illegal_u_turn: "#fd7e14", speeding: "#e64980",
  lane_violation: "#228be6", zebra_crossing: "#15aabf", highway_stop: "#fab005", red_light: "#be4bdb",
  pothole: "#82c91e", crack: "#12b886", waterlogging: "#4c6ef5", debris: "#868e96",
};

export const isAnomaly = (e: TwinEvent): boolean => e.kind === "anomaly";

export function pinStyle(e: TwinEvent): { color: string; size: number; label: string | null } {
  const color = TYPE_COLOR[e.type] ?? "#fa5252";
  // anomalies: smaller, with their type written next to them (Cesium points are round only)
  return isAnomaly(e) ? { color, size: 11, label: `▲ ${e.type}` } : { color, size: 14, label: null };
}

export interface Hotspot {
  x: number;
  y: number;
  count: number;
  by_type: Record<string, number>;
  lane_ids: string[];
}

export function gridHotspots(events: TwinEvent[], cell = HOTSPOT_CELL_M, top = HOTSPOT_TOP): Hotspot[] {
  const cells = new Map<string, TwinEvent[]>();
  for (const e of events) {
    if (!Number.isFinite(e.x) || !Number.isFinite(e.y)) continue;
    const k = `${Math.floor(e.x / cell)},${Math.floor(e.y / cell)}`;
    (cells.get(k) ?? cells.set(k, []).get(k)!).push(e);
  }
  return [...cells.values()]
    .sort((a, b) => b.length - a.length)
    .slice(0, top)
    .map((c) => {
      const by_type: Record<string, number> = {};
      c.forEach((e) => (by_type[e.type] = (by_type[e.type] ?? 0) + 1));
      return {
        x: c.reduce((s, e) => s + e.x, 0) / c.length,
        y: c.reduce((s, e) => s + e.y, 0) / c.length,
        count: c.length,
        by_type,
        lane_ids: [...new Set(c.map((e) => e.lane_id).filter((l): l is string => !!l))].sort().slice(0, 5),
      };
    });
}

export interface ProblemSection {
  lane_id: string;
  count: number;
  by_type: Record<string, number>;
}

/** Lanes ranked by events on them (violations and anomalies). */
export function problemSections(events: TwinEvent[], top = 8): ProblemSection[] {
  const m = new Map<string, ProblemSection>();
  for (const e of events) {
    if (!e.lane_id) continue;
    const s = m.get(e.lane_id) ?? { lane_id: e.lane_id, count: 0, by_type: {} };
    s.count += 1;
    s.by_type[e.type] = (s.by_type[e.type] ?? 0) + 1;
    m.set(e.lane_id, s);
  }
  return [...m.values()].sort((a, b) => b.count - a.count || a.lane_id.localeCompare(b.lane_id)).slice(0, top);
}

/** Recent positions of live vehicles: last TRAIL_S seconds of each track still on the socket. */
export class LiveTrails {
  private m = new Map<string, [number, number, number][]>(); // track -> [t_s, x, y]

  update(states: VehicleState[]): void {
    const seen = new Set<string>();
    for (const v of states) {
      const k = String(v.track_id);
      seen.add(k);
      const tr = this.m.get(k) ?? [];
      const t = v.t_s ?? 0;
      if (!tr.length || tr[tr.length - 1][0] !== t) tr.push([t, v.x, v.y]);
      while (tr.length && tr[0][0] < t - TRAIL_S) tr.shift();
      this.m.set(k, tr);
    }
    for (const k of [...this.m.keys()]) if (!seen.has(k)) this.m.delete(k);
  }

  get(trackId: string | number): [number, number][] {
    return (this.m.get(String(trackId)) ?? []).map((p) => [p[1], p[2]]);
  }

  clear(): void {
    this.m.clear();
  }
}
