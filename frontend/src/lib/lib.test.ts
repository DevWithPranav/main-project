import { filtersToParams, paramsToFilters } from './filters';
import { can } from './permissions';
import { binPoints, toBuckets, toHotspots, toHourly } from './stats';

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
    expect(binPoints([{ x: 1, y: 1 }, { x: 2, y: 3 }, { x: 15, y: 1 }], 10)).toHaveLength(2);
  });
});

describe('permissions', () => {
  it('match the API contract roles', () => {
    expect(can('OFFICER', 'review_violation')).toBe(true);
    expect(can('PLANNER', 'review_violation')).toBe(false);
    expect(can('MAINTENANCE', 'review_anomaly')).toBe(true);
    expect(can('OPERATOR', 'import_session')).toBe(true);
    expect(can(null, 'edit_profile')).toBe(false);
  });
});
