import type { Lane, LaneOverride } from '../api/types';
import {
  applyRoadOverrides, clickSeeds, COLOUR_SCHEMES, commonValue, describeItem, itemMatches, matchesGlob, normaliseAttributes,
  overridesFor, parseLaneId, scopeTarget, selectIds, selectionFromSeeds, speedColor, validateItem,
} from './roadEdits';

const lane = (id: string, extra: Partial<Lane> = {}): Lane => ({ id, road_id: id.split('_')[0].slice(1), centreline: [[0, 0], [1, 0]], ...extra });
const lanes: Lane[] = [
  lane('r46_s0_l-1', { road_class: 'urban', speed_limit_kmh: 30 }),
  lane('r46_s0_l1', { road_class: 'urban', speed_limit_kmh: 30 }),
  lane('r46_s1_l-1', { road_class: 'highway', speed_limit_kmh: 90, bridge: true }),
  lane('r460_s0_l-1', { road_class: 'urban' }),
  lane('r4_s0_l-1_p2', { lane_type: 'shoulder' }),
];

describe('glob matching (fnmatch)', () => {
  it('matches exact ids, * and ? like the engine', () => {
    expect(matchesGlob('r46_s0_l-1', 'r46_s0_l-1')).toBe(true);
    expect(matchesGlob('r46_s0_l-1', 'r46_s0_l-10')).toBe(false);
    expect(matchesGlob('r46_*', 'r46_s1_l-1')).toBe(true);
    expect(matchesGlob('r46_*', 'r460_s0_l-1')).toBe(false); // the underscore keeps road 460 out
    expect(matchesGlob('r46_s0_*', 'r46_s1_l-1')).toBe(false);
    expect(matchesGlob('r46_s?_l-1', 'r46_s1_l-1')).toBe(true);
    expect(matchesGlob('r4.*', 'r4_s0')).toBe(false); // dot is literal
  });

  it('parses lane ids and builds scope targets', () => {
    expect(parseLaneId('r4_s0_l-1_p2')).toEqual({ road: '4', section: '0', lane: '-1' });
    expect(parseLaneId('weird')).toBeNull();
    expect(scopeTarget('r46_s0_l-1', 'lane')).toBe('r46_s0_l-1');
    expect(scopeTarget('r46_s0_l-1', 'section')).toBe('r46_s0_*');
    expect(scopeTarget('r46_s0_l-1', 'road')).toBe('r46_*');
    expect(scopeTarget('weird', 'road')).toBe('weird');
  });
});

describe('selection', () => {
  it('widens a click to the section / road', () => {
    expect(selectIds('r46_s0_l-1', 'lane', lanes)).toEqual(['r46_s0_l-1']);
    expect(selectIds('r46_s0_l-1', 'section', lanes)).toEqual(['r46_s0_l-1', 'r46_s0_l1']);
    expect(selectIds('r46_s0_l-1', 'road', lanes)).toEqual(['r46_s0_l-1', 'r46_s0_l1', 'r46_s1_l-1']);
  });

  it('plain click replaces, shift-click adds and toggles off', () => {
    let seeds = clickSeeds([], 'r46_s0_l-1', 'lane', lanes, false);
    expect(seeds).toEqual(['r46_s0_l-1']);
    seeds = clickSeeds(seeds, 'r4_s0_l-1_p2', 'lane', lanes, true);
    expect([...selectionFromSeeds(seeds, 'lane', lanes)]).toEqual(['r46_s0_l-1', 'r4_s0_l-1_p2']);
    // switching scope re-widens the same clicks
    expect(selectionFromSeeds(seeds, 'road', lanes).size).toBe(4);
    // shift-click on an already selected road removes it
    seeds = clickSeeds(seeds, 'r46_s1_l-1', 'road', lanes, true);
    expect(seeds).toEqual(['r4_s0_l-1_p2']);
    expect(clickSeeds(seeds, 'r460_s0_l-1', 'lane', lanes, false)).toEqual(['r460_s0_l-1']);
  });
});

describe('mixed values', () => {
  it('reports the common value or mixed, with engine defaults for missing keys', () => {
    expect(commonValue(lanes.slice(0, 2), 'road_class')).toEqual({ value: 'urban', mixed: false });
    expect(commonValue(lanes.slice(0, 3), 'road_class')).toEqual({ value: null, mixed: true });
    expect(commonValue(lanes.slice(0, 2), 'bridge')).toEqual({ value: false, mixed: false }); // missing reads as false
    expect(commonValue([lanes[4]], 'lane_change')).toEqual({ value: 'both', mixed: false });
    expect(commonValue(lanes.slice(0, 2), 'restricted')).toEqual({ value: null, mixed: false });
    expect(commonValue([], 'ramp')).toEqual({ value: null, mixed: false });
  });
});

describe('override items and preview', () => {
  it('writes one glob per road / section, exact ids for lanes', () => {
    const sel = ['r46_s0_l-1', 'r46_s0_l1', 'r46_s1_l-1'];
    expect(overridesFor(sel, 'road', { road_class: 'highway', bridge: true })).toEqual([{ lane_id: 'r46_*', road_class: 'highway', bridge: true }]);
    expect(overridesFor(sel, 'section', { bridge: true }).map((o) => o.lane_id)).toEqual(['r46_s0_*', 'r46_s1_*']);
    expect(overridesFor(sel.slice(0, 2), 'lane', { ramp: null }, '  why ')).toEqual([
      { lane_id: 'r46_s0_l-1', ramp: null, note: 'why' }, { lane_id: 'r46_s0_l1', ramp: null, note: 'why' }]);
    expect(overridesFor(sel, 'lane', {})).toEqual([]);
  });

  it('applies items in order, later wins, and marks changed keys', () => {
    const items: LaneOverride[] = [
      { lane_id: 'r46_*', road_class: 'highway', speed_limit_kmh: 80 },
      { lane_id: 'r46_s0_l1', speed_limit_kmh: 50, restricted: 'bus' },
      { lane_id: 'r46_s1_*', bridge: false, note: 'not a bridge' },
    ];
    const out = applyRoadOverrides(lanes, items);
    const by = Object.fromEntries(out.map((l) => [l.id, l]));
    expect(by['r46_s0_l-1']).toMatchObject({ road_class: 'highway', speed_limit_kmh: 80, overridden: ['road_class', 'speed_limit_kmh'] });
    expect(by['r46_s0_l1']).toMatchObject({ speed_limit_kmh: 50, restricted: 'bus' });
    expect(by['r46_s0_l1'].overridden).toEqual(['road_class', 'speed_limit_kmh', 'restricted']);
    expect(by['r46_s1_l-1']).toMatchObject({ bridge: false, road_class: 'highway' });
    expect(by['r460_s0_l-1'].overridden).toBeUndefined();
    expect((by['r46_s1_l-1'] as unknown as Record<string, unknown>).note).toBeUndefined(); // note is not a lane attribute
    expect(lanes[0].road_class).toBe('urban'); // input untouched
  });

  it('keeps server-marked keys and counts matches', () => {
    const served = [{ ...lanes[0], overridden: ['speed_limit_kmh'] }];
    expect(applyRoadOverrides(served, [{ lane_id: 'r46_*', bridge: true }])[0].overridden).toEqual(['speed_limit_kmh', 'bridge']);
    expect(itemMatches({ lane_id: 'r46_*' }, lanes)).toBe(3);
    expect(itemMatches({ lane_id: 'r999_*' }, lanes)).toBe(0);
  });

  it('validates what the backend would refuse', () => {
    expect(validateItem({ lane_id: 'r46_*', road_class: 'highway', bridge: true, ramp: null })).toEqual([]);
    expect(validateItem({ lane_id: 'r46_*' })[0]).toMatch(/nothing to change/);
    expect(validateItem({ lane_id: 'x', speed_limit_kmh: 0 })[0]).toMatch(/> 0/);
    expect(validateItem({ lane_id: 'x', road_class: 'motorway' } as unknown as LaneOverride)[0]).toMatch(/urban, highway/);
    expect(validateItem({ lane_id: 'x', bridge: 'yes' } as unknown as LaneOverride)[0]).toMatch(/yes or no/);
  });

  it('describes items in plain words', () => {
    expect(describeItem({ lane_id: 'r46_*', road_class: 'highway', bridge: true, speed_limit_kmh: 80, ramp: null }))
      .toBe('road class highway, speed limit 80 km/h, bridge yes, ramp none');
  });
});

describe('attributes and colours', () => {
  it('normalises /api/road/attributes rows (ids or objects)', () => {
    expect(normaliseAttributes(null)).toBeNull();
    expect(normaliseAttributes({ detail: 'Not Found' })).toBeNull();
    const a = normaliseAttributes([{ key: 'bridge', label: 'Bridge', type: 'bool', drives: ['B5'] }, { key: 'ramp', type: 'enum', values: ['on', null], drives: [{ condition: 'B4', label: 'stop on ramp' }] }])!;
    expect(a[0].drives).toEqual([{ condition: 'B5', label: '' }]);
    expect(a[1]).toMatchObject({ label: 'ramp', drives: [{ condition: 'B4', label: 'stop on ramp' }] });
  });

  it('colours by attribute with graded speed', () => {
    expect(COLOUR_SCHEMES.road_class.color(lanes[2])).toBe('#4c6ef5');
    expect(COLOUR_SCHEMES.structure.color(lanes[2])).toBe('#7048e8');
    expect(COLOUR_SCHEMES.lane_type.color(lanes[4])).toBe('#f59f00');
    expect(speedColor(30)).toBe('#2f9e44');
    expect(speedColor(90)).toBe('#e03131');
    expect(speedColor(120)).toBe('#ae3ec9');
    expect(speedColor(null)).toBe('#adb5bd');
  });
});
