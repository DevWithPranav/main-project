/**
 * Lane-map -> triangle meshes in local ENU metres (pure, no Cesium; unit tested).
 *
 * Every lane becomes a ribbon along its centreline at its real road height (lane z from M1), with
 * its edge markings (solid / broken / double), medians, bridge piers. Meshes are flat
 * {positions, indices} arrays so the Cesium layer can batch ~900 lanes into a few primitives.
 *
 * Direction: a lane's centreline runs in its driving direction; "left" is the driver's left. In the
 * right-handed ENU frame (coords.ts) the left normal of direction (de, dn) is (-dn, de).
 */
import { carlaToEnu, type Vec3 } from "./coords";
import type { Lane, Zone } from "./types";

export interface Mesh {
  positions: number[]; // ENU xyz triples
  indices: number[];
}

// Lifts above the road height, so overlapping layers don't z-fight (visual only).
export const LIFT = { shoulder: 0.02, parking: 0.03, driving: 0.05, crosswalk: 0.07, marking: 0.09, median: 0.15 };
export const MARK_W = 0.15; // painted line width, m (typical road marking width; visual only)
export const DOUBLE_GAP = 0.15; // gap between the two lines of a double marking, m (visual only)
export const DASH_M = 3.0; // broken line: dash and gap lengths, m (visual only)
export const GAP_M = 6.0;
export const PIER_EVERY_M = 30; // one pier per this many metres of elevated lane
export const PIER_MIN_Z = 2.0; // only lanes at least this high get piers
const MAX_MITER = 2.0;

export const emptyMesh = (): Mesh => ({ positions: [], indices: [] });

/** Lane centreline in ENU with its height per point; consecutive duplicates dropped. */
export function laneEnu(lane: Lane): Vec3[] {
  const z = lane.z && lane.z.length === lane.centreline.length ? lane.z : null;
  const out: Vec3[] = [];
  lane.centreline.forEach(([x, y], i) => {
    const p = carlaToEnu(x, y, z ? z[i] : 0);
    const q = out[out.length - 1];
    if (!q || Math.hypot(p[0] - q[0], p[1] - q[1]) > 1e-3) out.push(p);
  });
  return out;
}

/** Cumulative length along a polyline (plan view). */
export function cumLength(pts: Vec3[]): number[] {
  const c = [0];
  for (let i = 1; i < pts.length; i++) c.push(c[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  return c;
}

/** Per-point left normals (2D, ENU), mitred at the joints: offsetting a point by n*d keeps both
 * adjacent segments exactly d away (clamped at sharp turns). */
export function leftNormals(pts: Vec3[]): [number, number][] {
  const segN: [number, number][] = [];
  for (let i = 0; i + 1 < pts.length; i++) {
    const dx = pts[i + 1][0] - pts[i][0];
    const dy = pts[i + 1][1] - pts[i][1];
    const L = Math.hypot(dx, dy) || 1;
    segN.push([-dy / L, dx / L]);
  }
  if (!segN.length) return pts.map(() => [0, 0]);
  return pts.map((_, i) => {
    const a = segN[Math.max(0, i - 1)];
    const b = segN[Math.min(segN.length - 1, i)];
    let nx = a[0] + b[0];
    let ny = a[1] + b[1];
    const L = Math.hypot(nx, ny);
    if (L < 1e-6) return b;
    nx /= L;
    ny /= L;
    const cos = nx * b[0] + ny * b[1];
    const s = Math.min(MAX_MITER, 1 / Math.max(cos, 1e-6));
    return [nx * s, ny * s];
  });
}

/** Strip between left offsets `a` and `b` (metres, + = driver's left) along pts, lifted by `lift`. */
export function addStrip(mesh: Mesh, pts: Vec3[], a: number, b: number, lift: number, normals = leftNormals(pts)): void {
  if (pts.length < 2) return;
  const base = mesh.positions.length / 3;
  for (let i = 0; i < pts.length; i++) {
    const [x, y, z] = pts[i];
    const [nx, ny] = normals[i];
    mesh.positions.push(x + nx * a, y + ny * a, z + lift, x + nx * b, y + ny * b, z + lift);
  }
  for (let i = 0; i + 1 < pts.length; i++) {
    const l0 = base + 2 * i, r0 = l0 + 1, l1 = l0 + 2, r1 = l0 + 3;
    mesh.indices.push(l0, r0, l1, r0, r1, l1);
  }
}

/** Vertical quad from (x0,y0) to (x1,y1), z from zb to zt. */
export function addWall(mesh: Mesh, x0: number, y0: number, x1: number, y1: number, zb: number, zt: number): void {
  const b = mesh.positions.length / 3;
  mesh.positions.push(x0, y0, zb, x1, y1, zb, x1, y1, zt, x0, y0, zt);
  mesh.indices.push(b, b + 1, b + 2, b, b + 2, b + 3);
}

/** Sub-polyline between arc lengths s0 and s1 (interpolated ends). */
export function slicePolyline(pts: Vec3[], cum: number[], s0: number, s1: number): Vec3[] {
  const at = (s: number): Vec3 => {
    let i = 1;
    while (i < cum.length - 1 && cum[i] < s) i++;
    const t = cum[i] > cum[i - 1] ? (s - cum[i - 1]) / (cum[i] - cum[i - 1]) : 0;
    const a = pts[i - 1], b = pts[i];
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  };
  const out: Vec3[] = [at(s0)];
  for (let i = 0; i < cum.length; i++) if (cum[i] > s0 && cum[i] < s1) out.push(pts[i]);
  out.push(at(s1));
  return out;
}

/** [start, end] arc lengths of the dashes of a broken line of the given length. */
export function dashRanges(length: number, dash = DASH_M, gap = GAP_M): [number, number][] {
  const out: [number, number][] = [];
  for (let s = 0; s < length; s += dash + gap) out.push([s, Math.min(s + dash, length)]);
  return out;
}

export type MarkColor = "white" | "yellow" | "curb" | "grass";

/** CARLA LaneMarkingType (lower case) -> the painted parts across the edge. `offset` is relative
 * to the edge (+ = further left of the lane's driving direction); null = nothing drawn. */
export function markingParts(type: string | undefined | null): { color: MarkColor; parts: { offset: number; dashed: boolean }[] } | null {
  const t = (type || "none").toLowerCase();
  const g = (MARK_W + DOUBLE_GAP) / 2;
  switch (t) {
    case "solid": return { color: "white", parts: [{ offset: 0, dashed: false }] };
    case "broken": return { color: "white", parts: [{ offset: 0, dashed: true }] };
    case "bottsdots": return { color: "white", parts: [{ offset: 0, dashed: true }] };
    // CARLA's exported map has no marking colour; doubles are drawn yellow as the usual centre line
    case "solidsolid": return { color: "yellow", parts: [{ offset: -g, dashed: false }, { offset: g, dashed: false }] };
    case "solidbroken": return { color: "yellow", parts: [{ offset: -g, dashed: false }, { offset: g, dashed: true }] };
    case "brokensolid": return { color: "yellow", parts: [{ offset: -g, dashed: true }, { offset: g, dashed: false }] };
    case "brokenbroken": return { color: "yellow", parts: [{ offset: -g, dashed: true }, { offset: g, dashed: true }] };
    case "curb": return { color: "curb", parts: [{ offset: 0, dashed: false }] };
    case "grass": return { color: "grass", parts: [{ offset: 0, dashed: false }] };
    default: return null;
  }
}

/** Which colour class a lane's surface gets (palette lives in roads.ts). Priority: restricted,
 * bridge, ramp, then lane type / road class. */
export function laneCategory(lane: Lane): string {
  const type = (lane.lane_type || "driving").toLowerCase();
  if (type === "shoulder" || type === "sidewalk") return "shoulder";
  if (type === "parking") return "parking";
  if (lane.restricted) return "restricted";
  if (lane.bridge) return "bridge";
  if (lane.ramp) return "ramp";
  if (lane.road_class === "highway") return "highway";
  if (lane.junction) return "junction";
  return "urban";
}

export interface LaneMeshes {
  surface: Mesh;
  marks: Record<MarkColor, Mesh>;
  median: Mesh;
  piers: Mesh;
}

/** Everything drawn for one lane, in ENU metres. */
export function buildLaneMeshes(lane: Lane): LaneMeshes {
  const pts = laneEnu(lane);
  const out: LaneMeshes = {
    surface: emptyMesh(),
    marks: { white: emptyMesh(), yellow: emptyMesh(), curb: emptyMesh(), grass: emptyMesh() },
    median: emptyMesh(),
    piers: emptyMesh(),
  };
  if (pts.length < 2) return out;
  const w = lane.width_m ?? 3.5;
  const type = (lane.lane_type || "driving").toLowerCase();
  const lift = type === "shoulder" ? LIFT.shoulder : type === "parking" ? LIFT.parking : LIFT.driving;
  const n = leftNormals(pts);
  addStrip(out.surface, pts, w / 2, -w / 2, lift, n);

  const cum = cumLength(pts);
  const length = cum[cum.length - 1];
  // junction lanes overlap each other; their edge lines would criss-cross the junction
  if (!lane.junction) {
    for (const [side, line] of [[1, lane.left_line], [-1, lane.right_line]] as const) {
      const m = markingParts(line);
      if (!m) continue;
      const edge = (side * w) / 2;
      const lw = m.color === "curb" ? 0.3 : m.color === "grass" ? 0.6 : MARK_W;
      for (const part of m.parts) {
        const off = edge + part.offset; // doubles straddle the edge
        if (!part.dashed) {
          addStrip(out.marks[m.color], pts, off + lw / 2, off - lw / 2, LIFT.marking, n);
        } else {
          for (const [s0, s1] of dashRanges(length)) {
            if (s1 - s0 < 0.2) continue;
            const seg = slicePolyline(pts, cum, s0, s1);
            addStrip(out.marks[m.color], seg, off + lw / 2, off - lw / 2, LIFT.marking);
          }
        }
      }
    }
  }
  if (lane.median_left) {
    // the separator sits between this lane's left edge and the opposing lane
    const gap = lane.median_gap_m && lane.median_gap_m > 0 ? lane.median_gap_m : 1.0;
    const mw = Math.min(Math.max(gap, 0.4), 4);
    addStrip(out.median, pts, w / 2 + gap / 2 + mw / 2, w / 2 + gap / 2 - mw / 2, LIFT.median, n);
  }
  if (lane.bridge || lane.tunnel) {
    for (let s = PIER_EVERY_M / 2; s < length; s += PIER_EVERY_M) {
      const [p] = slicePolyline(pts, cum, s, s);
      if (p[2] < PIER_MIN_Z) continue;
      const i = Math.min(cum.findIndex((c) => c >= s), n.length - 1);
      const [nx, ny] = n[Math.max(0, i)];
      const h = 0.6;
      addWall(out.piers, p[0] - nx * h, p[1] - ny * h, p[0] + nx * h, p[1] + ny * h, 0, p[2] - 0.3);
      addWall(out.piers, p[0] + ny * h, p[1] - nx * h, p[0] - ny * h, p[1] + nx * h, 0, p[2] - 0.3);
    }
  }
  return out;
}

/** Append mesh b to a (re-indexing b). */
export function mergeInto(a: Mesh, b: Mesh): Mesh {
  const base = a.positions.length / 3;
  for (const v of b.positions) a.positions.push(v);
  for (const i of b.indices) a.indices.push(i + base);
  return a;
}

/** Triangle fan of a (convex) polygon at height z + lift, ENU. */
export function polygonFan(poly: [number, number][], z: number, lift: number): Mesh {
  const m = emptyMesh();
  if (poly.length < 3) return m;
  for (const [x, y] of poly) {
    const [e, n] = carlaToEnu(x, y);
    m.positions.push(e, n, z + lift);
  }
  for (let i = 1; i + 1 < poly.length; i++) m.indices.push(0, i, i + 1);
  return m;
}

export function centroid(poly: [number, number][]): [number, number] {
  let x = 0, y = 0;
  for (const p of poly) {
    x += p[0];
    y += p[1];
  }
  return [x / poly.length, y / poly.length];
}

/** Road height lookup by plan position (CARLA x, y), from every lane's centreline points. Where
 * roads overlap (flyovers) it returns the level nearest `nearZ` (a vehicle's previous height). */
export class HeightIndex {
  private cells = new Map<string, number[]>(); // flat x, y, z triples
  constructor(lanes: Lane[], private readonly cell = 8) {
    for (const l of lanes) {
      const z = l.z && l.z.length === l.centreline.length ? l.z : null;
      l.centreline.forEach(([x, y], i) => {
        const k = this.key(x, y);
        let c = this.cells.get(k);
        if (!c) this.cells.set(k, (c = []));
        c.push(x, y, z ? z[i] : 0);
      });
    }
  }
  private key(x: number, y: number): string {
    return `${Math.floor(x / this.cell)},${Math.floor(y / this.cell)}`;
  }
  /** Heights of distinct road levels within `radius` m (nearest point per 2.5 m height band). */
  levels(x: number, y: number, radius = 6): { z: number; d: number }[] {
    const cx = Math.floor(x / this.cell), cy = Math.floor(y / this.cell), r = Math.ceil(radius / this.cell);
    const best = new Map<number, { z: number; d: number }>();
    for (let i = cx - r; i <= cx + r; i++)
      for (let j = cy - r; j <= cy + r; j++) {
        const c = this.cells.get(`${i},${j}`);
        if (!c) continue;
        for (let k = 0; k < c.length; k += 3) {
          const d = Math.hypot(c[k] - x, c[k + 1] - y);
          if (d > radius) continue;
          const band = Math.round(c[k + 2] / 2.5);
          const b = best.get(band);
          if (!b || d < b.d) best.set(band, { z: c[k + 2], d });
        }
      }
    return [...best.values()].sort((a, b) => a.d - b.d);
  }
  heightAt(x: number, y: number, nearZ?: number | null): number {
    const lv = this.levels(x, y);
    if (!lv.length) return 0;
    if (nearZ == null || lv.length === 1) return lv[0].z;
    return lv.reduce((a, b) => (Math.abs(b.z - nearZ) < Math.abs(a.z - nearZ) ? b : a)).z;
  }
}

/** Height of a lane near (x, y): z of its nearest centreline point (null when the lane has no z). */
export function laneHeightNear(lane: Lane, x: number, y: number): number | null {
  if (!lane.z || lane.z.length !== lane.centreline.length) return null;
  let best = Infinity, z = 0;
  lane.centreline.forEach(([px, py], i) => {
    const d = (px - x) ** 2 + (py - y) ** 2;
    if (d < best) {
      best = d;
      z = lane.z![i];
    }
  });
  return z;
}

export function crosswalkZones(zones: Zone[] | undefined): Zone[] {
  return (zones || []).filter((z) => z.type === "crosswalk" && z.polygon?.length >= 3);
}
