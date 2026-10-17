// /api/stats names its keys but not their shapes; these normalisers accept the plausible ones
// ({key: n}, [{key, count}], [[key, n]]) so the charts do not break on a backend detail.

export interface Bucket {
  key: string;
  count: number;
}

export interface Hotspot {
  x: number;
  y: number;
  count: number;
  label: string;
  type?: string;
  by_type?: Record<string, number>;
  lane_ids?: string[];
  radius_m?: number;
}

const KEY_FIELDS = ['key', 'type', 'condition', 'status', 'hour', 'name', 'id', 'label'];
const COUNT_FIELDS = ['count', 'n', 'value', 'total', 'events'];

function pick(o: Record<string, unknown>, fields: string[]): unknown {
  for (const f of fields) if (o[f] !== undefined && o[f] !== null) return o[f];
  return undefined;
}

export function toBuckets(x: unknown): Bucket[] {
  if (!x) return [];
  if (Array.isArray(x)) {
    return x
      .map((item): Bucket | null => {
        if (Array.isArray(item) && item.length >= 2) return { key: String(item[0]), count: Number(item[1]) };
        if (item && typeof item === 'object') {
          const o = item as Record<string, unknown>;
          const k = pick(o, KEY_FIELDS);
          const c = pick(o, COUNT_FIELDS);
          if (k !== undefined && c !== undefined) return { key: String(k), count: Number(c) };
        }
        return null;
      })
      .filter((b): b is Bucket => !!b && Number.isFinite(b.count));
  }
  if (typeof x === 'object') {
    return Object.entries(x as Record<string, unknown>)
      .map(([key, v]) => ({ key, count: Number(typeof v === 'object' && v ? pick(v as Record<string, unknown>, COUNT_FIELDS) : v) }))
      .filter((b) => Number.isFinite(b.count));
  }
  return [];
}

/** 24 hourly buckets (0..23); keys may be "0".."23", "07", "07:00" or ISO timestamps. */
export function toHourly(x: unknown): Bucket[] {
  const out = Array.from({ length: 24 }, (_, h) => ({ key: String(h).padStart(2, '0'), count: 0 }));
  const raw = toBuckets(x);
  // Timestamps (more than 24 distinct hours across days) are folded onto the hour of day.
  for (const b of raw) {
    let h: number;
    if (/^\d{1,2}(:00)?$/.test(b.key)) h = parseInt(b.key, 10);
    else {
      const d = new Date(b.key);
      if (Number.isNaN(d.getTime())) continue;
      h = d.getUTCHours();
    }
    if (h >= 0 && h < 24) out[h].count += b.count;
  }
  return out;
}

export function sortDesc(b: Bucket[]): Bucket[] {
  return [...b].sort((a, z) => z.count - a.count || a.key.localeCompare(z.key));
}

export function toHotspots(x: unknown): Hotspot[] {
  let list: unknown[] = [];
  if (Array.isArray(x)) list = x;
  else if (x && typeof x === 'object' && Array.isArray((x as { features?: unknown[] }).features))
    list = (x as { features: unknown[] }).features;
  else if (x && typeof x === 'object' && Array.isArray((x as { items?: unknown[] }).items))
    list = (x as { items: unknown[] }).items;
  const out: Hotspot[] = [];
  list.forEach((item, i) => {
    if (!item || typeof item !== 'object') return;
    let o = item as Record<string, unknown>;
    let x0: number | undefined;
    let y0: number | undefined;
    if (o.type === 'Feature' && o.geometry && typeof o.geometry === 'object') {
      const coords = (o.geometry as { coordinates?: number[] }).coordinates;
      if (coords) [x0, y0] = coords;
      o = { ...(o.properties as Record<string, unknown>) };
    }
    const cx = x0 ?? Number(pick(o, ['x', 'cx', 'center_x']));
    const cy = y0 ?? Number(pick(o, ['y', 'cy', 'center_y']));
    if (!Number.isFinite(cx) || !Number.isFinite(cy)) return;
    const count = Number(pick(o, COUNT_FIELDS) ?? 0);
    // backend/API.md: {x, y, cell_m, count, by_type: {type: n}, lane_ids: [...]}
    const byType = o.by_type && typeof o.by_type === 'object' ? (o.by_type as Record<string, number>) : undefined;
    const laneIds = Array.isArray(o.lane_ids) ? (o.lane_ids as unknown[]).map(String) : undefined;
    const top = byType ? Object.entries(byType).sort((a, b) => b[1] - a[1])[0]?.[0] : undefined;
    const named = pick(o, ['label', 'name', 'lane_id', 'zone_id', 'id']);
    const label = named !== undefined ? String(named)
      : laneIds?.length ? `lanes ${laneIds.slice(0, 2).join(', ')}${laneIds.length > 2 ? ' …' : ''}`
      : `(${cx.toFixed(0)}, ${cy.toFixed(0)})`;
    out.push({
      x: cx,
      y: cy,
      count: Number.isFinite(count) ? count : 0,
      label: label || `Hotspot ${i + 1}`,
      type: typeof o.type === 'string' ? o.type : top,
      by_type: byType,
      lane_ids: laneIds,
      radius_m: typeof o.radius_m === 'number' ? o.radius_m : typeof o.cell_m === 'number' ? o.cell_m / 2 : undefined,
    });
  });
  return out.sort((a, b) => b.count - a.count);
}

/** Grid-bins points for the map heat layer. */
export function binPoints(points: { x: number; y: number }[], cell: number): { x: number; y: number; n: number }[] {
  const m = new Map<string, { x: number; y: number; n: number }>();
  for (const p of points) {
    const gx = Math.floor(p.x / cell);
    const gy = Math.floor(p.y / cell);
    const k = `${gx},${gy}`;
    const c = m.get(k);
    if (c) c.n += 1;
    else m.set(k, { x: gx * cell, y: gy * cell, n: 1 });
  }
  return [...m.values()];
}
