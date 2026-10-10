/**
 * Planner edits (Expected_Output Section 5.3), pure and unit tested: what may be changed, input
 * validation, lane-override targets and the profile document the backend stores.
 *
 * Lane edits go into the profile's road.lane_overrides (schemas/profile.schema.json), which the
 * engine applies with road_features.apply_overrides (exact id or fnmatch glob; an entry matching no
 * lane is an error there, so it is refused here first). Zones have no inline place in the profile
 * schema (road.zones is a repo-relative file path), so drawn zones are exported in the
 * run_violations.merge_zones file format ({zones, lane_overrides}) and sent next to the profile.
 */
import type { Lane, LaneOverride, Zone } from "./types";

export const MAX_LIMIT_KMH = 130; // input sanity cap, not a legal limit
export const MIN_LIMIT_KMH = 5;
export const MIN_ZONE_AREA_M2 = 4; // smaller than a parked car: almost certainly a mis-click
export const MAX_ZONE_AREA_M2 = 50000;
export const MIN_NOTE_CHARS = 10;
export const NAME_RE = /^[A-Za-z0-9_.-]+$/; // profile.schema.json name pattern

/** Expected_Output Section 5.3, as shown to the planner. */
export const SUPPORTED_CHANGES: { change: string; ok: boolean; how: string }[] = [
  { change: "Speed limit of a lane / road", ok: true, how: "road.lane_overrides.speed_limit_kmh (engine + traffic-manager target speed)" },
  { change: "Restricted lane (bus / emergency)", ok: true, how: "road.lane_overrides.restricted (rule-side)" },
  { change: "No-parking / no-stopping / no-U-turn zone", ok: true, how: "zone polygon (merge_zones file), rule-side" },
  { change: "Signal timing", ok: false, how: "Not editable here: this lane map exports no signals (stop_lines empty); set via the CARLA API in a validation run" },
  { change: "Lane markings, lane-change rules", ok: false, how: "Refused: traffic follows them only in a regenerated OpenDRIVE world" },
  { change: "Road layout, one-way, closures, new signs", ok: false, how: "Refused: needs an edited OpenDRIVE loaded as a generated world" },
];

export function validateSpeed(v: unknown): string | null {
  const n = typeof v === "string" ? Number(v.trim()) : v;
  if (typeof n !== "number" || !Number.isFinite(n)) return "Speed limit must be a number (km/h)";
  if (n < MIN_LIMIT_KMH || n > MAX_LIMIT_KMH) return `Speed limit must be between ${MIN_LIMIT_KMH} and ${MAX_LIMIT_KMH} km/h`;
  return null;
}

export function validateNote(note: string): string | null {
  return note.trim().length >= MIN_NOTE_CHARS ? null : `Give a reason (at least ${MIN_NOTE_CHARS} characters): it is logged with the change`;
}

export function validateProfileName(name: string): string | null {
  return NAME_RE.test(name) ? null : "Profile name: letters, digits, _ . - only";
}

const LANE_ID_RE = /^r(-?\d+)_s(\d+)_l(-?\d+)(_p\d+)?$/;

/** Override target for a lane edit. "lane": the lane (all its _pN parts); "road_dir": every lane of
 * the same road running the same way (OpenDRIVE lane ids < 0 run with the road, > 0 against it). */
export function overrideTarget(laneId: string, scope: "lane" | "road_dir"): string {
  const m = LANE_ID_RE.exec(laneId);
  if (!m) return laneId;
  if (scope === "lane") return m[4] ? laneId.slice(0, -m[4].length) + "*" : laneId;
  return Number(m[3]) < 0 ? `r${m[1]}_s*_l-*` : `r${m[1]}_s*_l[0-9]*`;
}

/** fnmatch.fnmatchcase semantics (*, ?, [seq], [!seq]) as a RegExp. */
export function globToRegExp(glob: string): RegExp {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*") re += ".*";
    else if (c === "?") re += ".";
    else if (c === "[") {
      const j = glob.indexOf("]", i + 2);
      if (j < 0) re += "\\[";
      else {
        let body = glob.slice(i + 1, j);
        if (body.startsWith("!")) body = "^" + body.slice(1);
        re += "[" + body.replace(/\\/g, "\\\\") + "]";
        i = j;
      }
    } else re += c.replace(/[.+^${}()|\\]/g, "\\$&");
  }
  return new RegExp("^" + re + "$");
}

export function matchLanes(pattern: string, lanes: Lane[]): Lane[] {
  const re = globToRegExp(pattern);
  return lanes.filter((l) => re.test(l.id));
}

export function validateOverride(o: LaneOverride, lanes: Lane[]): string[] {
  const errs: string[] = [];
  if (!o.lane_id) errs.push("lane_id missing");
  if (o.speed_limit_kmh !== undefined) {
    const e = validateSpeed(o.speed_limit_kmh);
    if (e) errs.push(e);
  }
  if (o.restricted !== undefined && ![null, "bus", "emergency", "restricted"].includes(o.restricted)) errs.push("restricted must be bus, emergency, restricted or none");
  if (o.speed_limit_kmh === undefined && o.restricted === undefined) errs.push("nothing to change");
  const hit = matchLanes(o.lane_id, lanes);
  if (!hit.length) errs.push(`${o.lane_id} matches no lane in this scene`);
  else if (o.restricted && hit.some((l) => (l.lane_type || "driving") !== "driving")) errs.push("only driving lanes can be restricted");
  return errs;
}

/** Existing overrides with `add` merged in (same lane_id: fields replaced). */
export function mergeOverrides(existing: LaneOverride[], add: LaneOverride[]): LaneOverride[] {
  const out = existing.map((o) => ({ ...o }));
  for (const a of add) {
    const i = out.findIndex((o) => o.lane_id === a.lane_id);
    if (i >= 0) out[i] = { ...out[i], ...a };
    else out.push({ ...a });
  }
  return out;
}

/** Scene lanes with overrides applied (preview of what the engine will read). */
export function applyOverrides(lanes: Lane[], overrides: LaneOverride[]): Lane[] {
  const out = lanes.map((l) => ({ ...l }));
  for (const o of overrides) {
    const re = globToRegExp(o.lane_id);
    for (const l of out) if (re.test(l.id)) {
      if (o.speed_limit_kmh !== undefined) l.speed_limit_kmh = o.speed_limit_kmh;
      if (o.restricted !== undefined) l.restricted = o.restricted;
    }
  }
  return out;
}

export function polygonArea(poly: [number, number][]): number {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const [x0, y0] = poly[i], [x1, y1] = poly[(i + 1) % poly.length];
    a += x0 * y1 - x1 * y0;
  }
  return Math.abs(a) / 2;
}

function segmentsCross(a: number[], b: number[], c: number[], d: number[]): boolean {
  const o = (p: number[], q: number[], r: number[]) => Math.sign((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]));
  return o(a, b, c) * o(a, b, d) < 0 && o(c, d, a) * o(c, d, b) < 0;
}

export function selfIntersects(poly: [number, number][]): boolean {
  const n = poly.length;
  for (let i = 0; i < n; i++)
    for (let j = i + 2; j < n; j++) {
      if (i === 0 && j === n - 1) continue; // adjacent through the closing edge
      if (segmentsCross(poly[i], poly[(i + 1) % n], poly[j], poly[(j + 1) % n])) return true;
    }
  return false;
}

export const ZONE_TYPES = ["no_parking", "no_u_turn"] as const;

export function validateZone(z: Zone): string[] {
  const errs: string[] = [];
  if (!NAME_RE.test(z.id)) errs.push("zone id: letters, digits, _ . - only");
  if (!(ZONE_TYPES as readonly string[]).includes(z.type)) errs.push(`zone type must be one of ${ZONE_TYPES.join(", ")}`);
  if (z.polygon.length < 3) errs.push("a zone needs at least 3 points");
  else {
    const a = polygonArea(z.polygon);
    if (a < MIN_ZONE_AREA_M2) errs.push(`zone too small (${a.toFixed(1)} m², minimum ${MIN_ZONE_AREA_M2})`);
    if (a > MAX_ZONE_AREA_M2) errs.push(`zone too large (${a.toFixed(0)} m², maximum ${MAX_ZONE_AREA_M2})`);
    if (selfIntersects(z.polygon)) errs.push("zone outline crosses itself");
  }
  if (z.grace_s !== undefined && !(typeof z.grace_s === "number" && z.grace_s >= 0)) errs.push("grace_s must be ≥ 0");
  return errs;
}

/** The new profile document: base profile (deep copy) with the merged lane overrides. */
export function buildProfile(base: Record<string, any>, name: string, overrides: LaneOverride[]): Record<string, any> {
  const p = JSON.parse(JSON.stringify(base || {}));
  p.profile_version = 1;
  p.name = name;
  p.road = p.road || {};
  const merged = mergeOverrides(p.road.lane_overrides || [], overrides);
  if (merged.length) p.road.lane_overrides = merged;
  return p;
}

/** run_violations.py --zones file format (merge_zones): extra zones + lane overrides. */
export function zonesFile(scene: string, zones: Zone[], overrides: LaneOverride[]): Record<string, unknown> {
  return { scene, source: "twin planner edit", zones, lane_overrides: overrides };
}
