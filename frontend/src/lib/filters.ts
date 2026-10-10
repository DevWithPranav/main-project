// Event filters shared by /api/events, /api/stats and /api/export (backend/API.md), and their
// round trip through the page URL so a filtered view can be bookmarked or shared.

export type ReviewFilter = 'none' | 'confirmed' | 'dismissed';
export type KindFilter = 'violation' | 'anomaly';

export interface EventFilters {
  session_id?: string;
  type?: string;
  condition?: string;
  status?: string;
  review?: ReviewFilter;
  kind?: KindFilter;
  since?: string;
  until?: string;
  /** x0,y0,x1,y1 in map metres */
  bbox?: [number, number, number, number];
}

export interface PageOpts {
  limit?: number;
  offset?: number;
}

const STRING_KEYS = ['session_id', 'type', 'condition', 'status', 'since', 'until'] as const;
const REVIEWS: ReviewFilter[] = ['none', 'confirmed', 'dismissed'];
const KINDS: KindFilter[] = ['violation', 'anomaly'];

/** Filters (+ paging) as query params; empty values are left out. */
export function filtersToParams(f: EventFilters, page: PageOpts = {}): URLSearchParams {
  const p = new URLSearchParams();
  for (const k of STRING_KEYS) {
    const v = f[k];
    if (v !== undefined && v !== null && String(v).trim() !== '') p.set(k, String(v).trim());
  }
  if (f.review) p.set('review', f.review);
  if (f.kind) p.set('kind', f.kind);
  if (f.bbox && f.bbox.length === 4 && f.bbox.every(Number.isFinite)) {
    const [x0, y0, x1, y1] = f.bbox;
    p.set('bbox', [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)].join(','));
  }
  if (page.limit !== undefined) p.set('limit', String(page.limit));
  if (page.offset !== undefined && page.offset > 0) p.set('offset', String(page.offset));
  return p;
}

/** Inverse of filtersToParams; unknown or malformed values are dropped. */
export function paramsToFilters(p: URLSearchParams): EventFilters {
  const f: EventFilters = {};
  for (const k of STRING_KEYS) {
    const v = p.get(k);
    if (v) f[k] = v;
  }
  const review = p.get('review') as ReviewFilter | null;
  if (review && REVIEWS.includes(review)) f.review = review;
  const kind = p.get('kind') as KindFilter | null;
  if (kind && KINDS.includes(kind)) f.kind = kind;
  const bbox = p.get('bbox');
  if (bbox) {
    const n = bbox.split(',').map(Number);
    if (n.length === 4 && n.every(Number.isFinite)) f.bbox = n as [number, number, number, number];
  }
  return f;
}

export function activeFilterCount(f: EventFilters): number {
  return Object.values(f).filter((v) => v !== undefined && v !== null && v !== '').length;
}

/** Same filtering the backend does, for the mock API and for client-side replay lists. */
export function matchesFilters(
  e: {
    session_id?: string;
    type: string;
    condition?: string | null;
    status: string;
    review?: { outcome: string } | null;
    kind?: string;
    x: number;
    y: number;
    at?: string;
  },
  f: EventFilters,
): boolean {
  if (f.session_id && e.session_id !== f.session_id) return false;
  if (f.type && e.type !== f.type) return false;
  if (f.condition && e.condition !== f.condition) return false;
  if (f.status && e.status !== f.status) return false;
  if (f.kind && (e.kind ?? 'violation') !== f.kind) return false;
  if (f.review) {
    const outcome = e.review?.outcome ?? 'none';
    if (outcome !== f.review) return false;
  }
  if (f.bbox) {
    const [x0, y0, x1, y1] = f.bbox;
    if (e.x < Math.min(x0, x1) || e.x > Math.max(x0, x1) || e.y < Math.min(y0, y1) || e.y > Math.max(y0, y1))
      return false;
  }
  if ((f.since || f.until) && e.at) {
    const t = Date.parse(e.at);
    if (f.since && t < Date.parse(f.since)) return false;
    if (f.until && t > Date.parse(f.until)) return false;
  }
  return true;
}
