import type { Condition, TrafficEvent } from '../api/types';
import { eventTime, isAnomaly } from '../api/types';

export const TYPE_LABELS: Record<string, string> = {
  no_parking: 'No parking',
  wrong_way: 'Wrong way',
  illegal_u_turn: 'Illegal U-turn',
  speeding: 'Speeding',
  lane_violation: 'Lane violation',
  zebra_crossing: 'Zebra crossing',
  highway_stop: 'Highway stop',
  red_light: 'Red light',
  pothole: 'Pothole',
  crack: 'Crack',
  waterlogging: 'Waterlogging',
  debris: 'Debris',
};

export const TYPE_COLORS: Record<string, string> = {
  no_parking: 'violet',
  wrong_way: 'red',
  illegal_u_turn: 'orange',
  speeding: 'pink',
  lane_violation: 'blue',
  zebra_crossing: 'cyan',
  highway_stop: 'yellow',
  red_light: 'grape',
  pothole: 'lime',
  crack: 'teal',
  waterlogging: 'indigo',
  debris: 'gray',
};

export const STATUS_COLORS: Record<string, string> = {
  flagged: 'red',
  needs_review: 'yellow',
  suppressed: 'gray',
  possible_breakdown: 'orange',
  reviewed: 'blue',
  work_order_issued: 'grape',
  repaired: 'green',
};

export const typeLabel = (t: string) => TYPE_LABELS[t] ?? t.replace(/_/g, ' ');
export const statusLabel = (s: string) => s.replace(/_/g, ' ');

export function conditionIndex(conditions: Condition[] | undefined): Map<string, Condition> {
  return new Map((conditions ?? []).map((c) => [c.id, c]));
}

export function conditionLabel(id: string | null | undefined, idx: Map<string, Condition>): string {
  if (!id) return '—';
  const c = idx.get(id);
  return c ? `${id} · ${c.name}` : id;
}

/** Sim/flight seconds as m:ss.s (events carry sim time, not wall-clock). */
export function fmtSimTime(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return '—';
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return `${m}:${sec.toFixed(1).padStart(4, '0')}`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export const fmtNum = (v: number, digits = 1) => (Number.isInteger(v) ? String(v) : v.toFixed(digits));

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return fmtNum(v, 2);
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'string') return v;
  return JSON.stringify(v);
}

export function eventSummary(e: TrafficEvent): string {
  if (isAnomaly(e)) return `${typeLabel(e.type)} · severity ${fmtNum(e.severity_score, 2)}`;
  return `${typeLabel(e.type)} · ${e.cls} #${e.track_ids.join(', #')} · t=${fmtSimTime(eventTime(e))}`;
}

export function reviewState(e: TrafficEvent): 'none' | 'confirmed' | 'dismissed' {
  return e.review?.outcome ?? 'none';
}
