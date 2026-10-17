import { filtersToParams, paramsToFilters } from './filters';
import { can } from './permissions';
import { binPoints, toBuckets, toHotspots, toHourly } from './stats';
import { sessionToVideo, trailOf, videoToSession } from './videoSync';

describe('filters', () => {
  it('round-trips through URL params and drops empty values', () => {
    const f = { session_id: '20261010_120643', type: 'speeding', review: 'none' as const, bbox: [0, 1, 2, 3] as [number, number, number, number] };
    const p = filtersToParams({ ...f, status: '' }, { limit: 50, offset: 100 });
    expect(p.get('status')).toBeNull();
    expect(p.get('limit')).toBe('50');
    expect(paramsToFilters(p)).toMatchObject(f);
  });
});

describe('stats normalisers', () => {
  it('accept object, pair and row shapes', () => {
    expect(toBuckets({ a: 2, b: 1 })).toEqual([{ key: 'a', count: 2 }, { key: 'b', count: 1 }]);
    expect(toBuckets([['a', 3]])).toEqual([{ key: 'a', count: 3 }]);
    expect(toBuckets([{ type: 'speeding', count: 4 }])).toEqual([{ key: 'speeding', count: 4 }]);
    const hours = toHourly({ 10: 1, 9: 2 });
    expect(hours).toHaveLength(24);
    expect(hours[9]).toEqual({ key: '09', count: 2 });
    expect(hours[10].count).toBe(1);
  });
  it('sorts hotspots by count and bins points', () => {
    const h = toHotspots([{ x: 0, y: 0, count: 1 }, { x: 5, y: 5, count: 9 }]);
    expect(h[0].count).toBe(9);
    const b = toHotspots([{ x: 3, y: 4, cell_m: 25, count: 5, by_type: { speeding: 1, wrong_way: 4 }, lane_ids: ['1_0_-1', '2_0_1', '3_0_1'] }]);
    expect(b[0]).toMatchObject({ type: 'wrong_way', label: 'lanes 1_0_-1, 2_0_1 …', radius_m: 12.5 });
    expect(binPoints([{ x: 1, y: 1 }, { x: 2, y: 3 }, { x: 15, y: 1 }], 10)).toHaveLength(2);
  });
});

describe('permissions', () => {
  it('match the API contract roles', () => {
    expect(can('OFFICER', 'review_violation')).toBe(true);
    expect(can('PLANNER', 'review_violation')).toBe(false);
    expect(can('MAINTENANCE', 'review_anomaly')).toBe(true);
    expect(can('OFFICER', 'review_anomaly')).toBe(false); // API.md: anomalies MAINTENANCE / ADMIN
    expect(can('OPERATOR', 'import_session')).toBe(true);
    expect(can(null, 'edit_profile')).toBe(false);
  });
});

describe('video sync', () => {
  const ft = [10, 10.2, 10.4, 10.6, 10.8]; // every 2nd source frame, 5 fps output
  it('maps video time to session time and back', () => {
    expect(videoToSession(0.4, ft, 5)).toBe(10.4);
    expect(videoToSession(99, ft, 5)).toBe(10.8);
    expect(sessionToVideo(10.5, ft, 5)).toBeCloseTo(0.4);
    expect(sessionToVideo(5, ft, 5)).toBe(0);
    expect(sessionToVideo(20, ft, 5)).toBeCloseTo(0.8);
  });
  it('keeps the last 3 s as a trail', () => {
    const s: [number, number, number, number][] = [[0, 0, 0, 0], [2, 1, 0, 0], [4, 2, 0, 0], [6, 3, 0, 0]];
    expect(trailOf(s, 5)).toEqual([[1, 0], [2, 0]]);
  });
});
