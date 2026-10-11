// Road configuration edits (Configuration > Road configuration and the 3D twin's planner panel):
// which lanes a click selects (lane / road section / whole road), the profile's road.lane_overrides
// items those edits become, and a client-side preview of them (same matching as the engine's
// road_features.apply_overrides: exact id or fnmatch glob, later items win). Pure, unit tested.

import { globToRegExp } from '../twin/planEdits';
import type { Lane, LaneOverride, RoadAttribute } from '../api/types';

export type SelectScope = 'lane' | 'section' | 'road';

/** Keys an override item may set (besides lane_id and note), in panel order. */
export const OVERRIDE_KEYS = [
  'road_class', 'lane_type', 'speed_limit_kmh', 'lane_change', 'one_way', 'bridge', 'tunnel', 'ramp', 'median_left', 'restricted',
] as const;
export type OverrideKey = (typeof OVERRIDE_KEYS)[number];
export type AttrValue = string | number | boolean | null;

/** Used when GET /api/road/attributes is not there (copy of ml/violation_engine/road_features.py ROAD_ATTRIBUTES). */
export const FALLBACK_ATTRIBUTES: RoadAttribute[] = [
  { key: 'road_class', label: 'Road class', type: 'enum', values: ['urban', 'highway'],
    drives: [{ condition: 'B1', label: 'stopping on a highway' }, { condition: 'B2', label: 'stopping on a highway shoulder' }, { condition: 'C3', label: 'wrong way on a divided highway' }] },
  { key: 'lane_type', label: 'Lane type', type: 'enum', values: ['driving', 'shoulder', 'parking'],
    drives: [{ condition: 'A6', label: 'driving on the shoulder' }, { condition: 'B2', label: 'stopping on a highway shoulder' },
      { condition: 'A1-A4, A8', label: 'lane discipline (driving lanes only)' }, { condition: 'C1-C5', label: 'wrong way (driving lanes only)' },
      { condition: 'D2-D4', label: 'U-turns (driving lanes only)' }] },
  { key: 'speed_limit_kmh', label: 'Speed limit (km/h)', type: 'number',
    drives: [{ condition: 'E1', label: 'speeding' }, { condition: 'E4', label: 'class speed limit' }] },
  { key: 'lane_change', label: 'Lane change allowed', type: 'enum', values: ['none', 'left', 'right', 'both'],
    drives: [{ condition: 'A3', label: 'prohibited lane change' }] },
  { key: 'one_way', label: 'One-way road', type: 'bool', drives: [{ condition: 'C5', label: 'wrong way on a one-way road' }] },
  { key: 'bridge', label: 'Bridge', type: 'bool', drives: [{ condition: 'B5', label: 'stopping on a bridge / in a tunnel' }] },
  { key: 'tunnel', label: 'Tunnel', type: 'bool', drives: [{ condition: 'B5', label: 'stopping on a bridge / in a tunnel' }] },
  { key: 'ramp', label: 'Ramp', type: 'enum', values: ['on', 'off', 'link', null],
    drives: [{ condition: 'B4', label: 'stopping on a ramp' }, { condition: 'C4', label: 'wrong way on a ramp' }] },
  { key: 'median_left', label: 'Median on the left', type: 'bool', drives: [{ condition: 'D4', label: 'U-turn through the median' }] },
  { key: 'restricted', label: 'Restricted lane', type: 'enum', values: ['bus', 'emergency', 'restricted', null],
    drives: [{ condition: 'A5', label: 'using a restricted lane' }] },
];

/** /api/road/attributes rows may list drives as ids or {condition, label}; normalise to the latter. */
export function normaliseAttributes(rows: unknown): RoadAttribute[] | null {
  if (!Array.isArray(rows) || !rows.length) return null;
  const out: RoadAttribute[] = [];
  for (const r of rows as Record<string, unknown>[]) {
    if (!r || typeof r.key !== 'string') continue;
    const drives = (Array.isArray(r.drives) ? r.drives : []).map((d) =>
      typeof d === 'string' ? { condition: d, label: '' } : { condition: String((d as { condition?: unknown }).condition ?? ''), label: String((d as { label?: unknown }).label ?? '') });
    out.push({ ...(r as unknown as RoadAttribute), label: String(r.label ?? r.key), drives });
  }
  return out.length ? out : null;
}

/** What a missing key reads as (road_features._READ_AS). */
const READ_AS: Partial<Record<OverrideKey, AttrValue>> = {
  lane_type: 'driving', lane_change: 'both', road_class: 'urban', bridge: false, tunnel: false, median_left: false, one_way: false,
  ramp: null, restricted: null, speed_limit_kmh: null,
};

export function laneValue(l: Lane, key: OverrideKey): AttrValue {
  const v = (l as unknown as Record<string, unknown>)[key];
  return v === undefined || v === null ? (READ_AS[key] ?? null) : (v as AttrValue);
}

const LANE_ID_RE = /^r(-?\d+)_s(\d+)_l(-?\d+)(_p\d+)?$/;

export function parseLaneId(id: string): { road: string; section: string; lane: string } | null {
  const m = LANE_ID_RE.exec(id);
  return m ? { road: m[1], section: m[2], lane: m[3] } : null;
}

/** The lane_id an edit of `laneId` writes for a scope: the exact id, `r46_s0_*` or `r46_*`. */
export function scopeTarget(laneId: string, scope: SelectScope): string {
  const p = parseLaneId(laneId);
  if (!p || scope === 'lane') return laneId;
  return scope === 'section' ? `r${p.road}_s${p.section}_*` : `r${p.road}_*`;
}

export function matchesGlob(pattern: string, id: string): boolean {
  return /[*?[]/.test(pattern) ? globToRegExp(pattern).test(id) : pattern === id;
}

/** Lane ids a click on `laneId` selects for a scope. */
export function selectIds(laneId: string, scope: SelectScope, lanes: Lane[]): string[] {
  const t = scopeTarget(laneId, scope);
  if (t === laneId) return [laneId];
  return lanes.filter((l) => matchesGlob(t, l.id)).map((l) => l.id);
}

/** The selection is the clicked lanes ("seeds") widened to the scope, so switching the scope after a
 * click widens / narrows it. */
export function selectionFromSeeds(seeds: string[], scope: SelectScope, lanes: Lane[]): Set<string> {
  const out = new Set<string>();
  for (const s of seeds) selectIds(s, scope, lanes).forEach((i) => out.add(i));
  return out;
}

/** Click: plain click starts a new selection; shift-click adds the lane's group, or removes it when
 * it is already selected. */
export function clickSeeds(seeds: string[], laneId: string, scope: SelectScope, lanes: Lane[], add: boolean): string[] {
  if (!add) return [laneId];
  const group = new Set(selectIds(laneId, scope, lanes));
  const sel = selectionFromSeeds(seeds, scope, lanes);
  if ([...group].every((i) => sel.has(i)))
    return seeds.filter((s) => !selectIds(s, scope, lanes).some((i) => group.has(i)));
  return [...seeds, laneId];
}

/** The value shared by every selected lane, or mixed. */
export function commonValue(lanes: Lane[], key: OverrideKey): { value: AttrValue; mixed: boolean } {
  if (!lanes.length) return { value: null, mixed: false };
  const first = laneValue(lanes[0], key);
  for (const l of lanes) if (laneValue(l, key) !== first) return { value: null, mixed: true };
  return { value: first, mixed: false };
}

/** Override items for the selected lanes: one glob per road / section in those scopes, else exact ids. */
export function overridesFor(selected: string[], scope: SelectScope, patch: Partial<Record<OverrideKey, AttrValue>>, note?: string): LaneOverride[] {
  if (!Object.keys(patch).length) return [];
  const targets: string[] = [];
  for (const id of selected) {
    const t = scopeTarget(id, scope);
    if (!targets.includes(t)) targets.push(t);
  }
  return targets.map((lane_id) => ({ lane_id, ...patch, ...(note?.trim() ? { note: note.trim() } : {}) }) as LaneOverride);
}

export type PreviewLane = Lane & { overridden?: string[] };

/** Lanes with the override items applied in order (later wins); changed lanes get `overridden` keys.
 * Keys already in a lane's `overridden` (server-applied) are kept. */
export function applyRoadOverrides<L extends Lane>(lanes: L[], overrides: LaneOverride[]): (L & { overridden?: string[] })[] {
  const out = lanes.map((l) => ({ ...l })) as (L & { overridden?: string[] })[];
  for (const o of overrides) {
    const keys = OVERRIDE_KEYS.filter((k) => k in o);
    if (!keys.length) continue;
    for (const l of out) {
      if (!matchesGlob(o.lane_id, l.id)) continue;
      const rec = l as unknown as Record<string, unknown>;
      for (const k of keys) rec[k] = (o as unknown as Record<string, unknown>)[k];
      l.overridden = [...new Set([...(l.overridden ?? []), ...keys])];
    }
  }
  return out;
}

/** Lane ids an item matches (empty = the engine refuses it). */
export function itemMatches(o: LaneOverride, lanes: Lane[]): number {
  return lanes.reduce((n, l) => n + (matchesGlob(o.lane_id, l.id) ? 1 : 0), 0);
}

/** Problems the backend would refuse, checked before saving. */
export function validateItem(o: LaneOverride, attrs: RoadAttribute[] = FALLBACK_ATTRIBUTES): string[] {
  const errs: string[] = [];
  if (!o.lane_id?.trim()) errs.push('lane_id missing');
  const rec = o as unknown as Record<string, unknown>;
  const keys = OVERRIDE_KEYS.filter((k) => k in o);
  if (!keys.length) errs.push(`${o.lane_id}: nothing to change`);
  for (const k of keys) {
    const v = rec[k];
    const a = attrs.find((x) => x.key === k);
    if (k === 'speed_limit_kmh') {
      if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) errs.push(`${o.lane_id}: speed limit must be a number > 0`);
    } else if (a?.type === 'bool') {
      if (typeof v !== 'boolean') errs.push(`${o.lane_id}: ${k} must be yes or no`);
    } else if (a?.type === 'enum' && a.values && !a.values.includes(v as string | null)) {
      errs.push(`${o.lane_id}: ${k} must be one of ${a.values.map((x) => x ?? 'none').join(', ')}`);
    }
  }
  return errs;
}

/** Short text of an item for the lists: `road class highway, bridge yes`. */
export function describeItem(o: LaneOverride, attrs: RoadAttribute[] = FALLBACK_ATTRIBUTES): string {
  const rec = o as unknown as Record<string, unknown>;
  return OVERRIDE_KEYS.filter((k) => k in o)
    .map((k) => {
      const v = rec[k];
      const label = (attrs.find((a) => a.key === k)?.label ?? k).replace(/ \(km\/h\)$/, '').toLowerCase();
      return `${label} ${v === true ? 'yes' : v === false ? 'no' : v === null ? 'none' : k === 'speed_limit_kmh' ? `${v} km/h` : v}`;
    })
    .join(', ');
}

// ---------------------------------------------------------------- colour by

export type ColourBy = 'road_class' | 'structure' | 'ramp' | 'lane_type' | 'lane_change' | 'one_way' | 'median' | 'restricted' | 'speed';

export interface LegendItem { label: string; color: string }
export interface ColourScheme { label: string; legend: LegendItem[]; color: (l: Lane) => string }

export const NEUTRAL = '#adb5bd';

const SPEED_BANDS: [number, string, string][] = [
  [30, '#2f9e44', '≤ 30'], [40, '#74b816', '40'], [50, '#fab005', '50'], [70, '#f76707', '60-70'], [90, '#e03131', '80-90'], [Infinity, '#ae3ec9', '≥ 100'],
];

export function speedColor(v: number | null | undefined): string {
  if (v == null || !(v > 0)) return NEUTRAL;
  return SPEED_BANDS.find(([max]) => v <= max)![1];
}

function byValue(label: string, key: OverrideKey, entries: [AttrValue, string, string][]): ColourScheme {
  return {
    label,
    legend: entries.map(([, color, l]) => ({ label: l, color })),
    color: (l) => entries.find(([v]) => v === laneValue(l, key))?.[1] ?? NEUTRAL,
  };
}

export const COLOUR_SCHEMES: Record<ColourBy, ColourScheme> = {
  road_class: byValue('Road class', 'road_class', [['highway', '#4c6ef5', 'highway'], ['urban', NEUTRAL, 'urban']]),
  structure: {
    label: 'Bridge / tunnel',
    legend: [{ label: 'bridge', color: '#7048e8' }, { label: 'tunnel', color: '#e8590c' }, { label: 'neither', color: NEUTRAL }],
    color: (l) => (l.tunnel ? '#e8590c' : l.bridge ? '#7048e8' : NEUTRAL),
  },
  ramp: byValue('Ramp', 'ramp', [['on', '#2f9e44', 'on-ramp'], ['off', '#e03131', 'off-ramp'], ['link', '#f59f00', 'link'], [null, NEUTRAL, 'none']]),
  lane_type: byValue('Lane type', 'lane_type', [['driving', '#4c6ef5', 'driving'], ['shoulder', '#f59f00', 'shoulder'], ['parking', '#12b886', 'parking']]),
  lane_change: byValue('Lane change', 'lane_change', [['none', '#e03131', 'none'], ['left', '#1c7ed6', 'left only'], ['right', '#f76707', 'right only'], ['both', '#2f9e44', 'both']]),
  one_way: byValue('One-way', 'one_way', [[true, '#15aabf', 'one-way'], [false, NEUTRAL, 'two-way / unknown']]),
  median: byValue('Median', 'median_left', [[true, '#e64980', 'median on the left'], [false, NEUTRAL, 'none']]),
  restricted: byValue('Restricted', 'restricted', [['bus', '#f76707', 'bus'], ['emergency', '#e03131', 'emergency'], ['restricted', '#9c36b5', 'restricted'], [null, NEUTRAL, 'open']]),
  speed: {
    label: 'Speed limit',
    legend: [...SPEED_BANDS.map(([, color, label]) => ({ label: `${label} km/h`, color })), { label: 'none', color: NEUTRAL }],
    color: (l) => speedColor(l.speed_limit_kmh),
  },
};
