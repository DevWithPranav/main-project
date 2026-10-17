import { describe, expect, it } from 'vitest';
import type { PlanAnalysis, PlanCatalogue, PlanKind } from '../../api/types';
import { alternativesHtml, beforeAfterChart, bestWorstHtml, compareHtml, esc, evidenceBadge, messagesHtml, observedHtml, replayHtml } from './recoView';
import { applyAlternative, countItems, defaultsFor, describeItem, emptyPlan, footprint, paramProblems, PlanStore, rectPolygon } from './planStore';

const speed: PlanKind = {
  key: 'speed_limit', group: 'rule', label: 'Speed limit', target: 'lane',
  params: [{ key: 'speed_kmh', label: 'Limit', type: 'number', default: 50, min: 5, max: 130, unit: 'km/h' }, { key: 'scope', label: 'Applies to', type: 'enum', default: 'lane', options: ['lane', 'section', 'road'] }],
  replay: { mode: 'speed_limit', note: '' }, carla: { status: 'supported', how: '' },
};
const zone: PlanKind = {
  key: 'no_parking_zone', group: 'prop', label: 'No-parking zone', target: 'area',
  params: [{ key: 'length_m', label: 'Length', type: 'number', default: 20 }, { key: 'width_m', label: 'Width', type: 'number', default: 4 }, { key: 'grace_s', label: 'Grace', type: 'number', default: 30 }],
  replay: { mode: 'zone:no_parking', note: '' }, carla: { status: 'approximate', how: '' },
};
const signal: PlanKind = { key: 'traffic_signal', group: 'prop', label: 'Traffic signal', target: 'point', params: [], replay: { mode: 'none', note: 'x' }, carla: { status: 'unsupported', how: '' } };
const cat: PlanCatalogue = {
  kinds: { speed_limit: speed, no_parking_zone: zone, traffic_signal: signal }, groups: ['prop', 'rule', 'scenario'],
  templates: { t1: { label: 'T1', items: [{ group: 'rule', kind: 'speed_limit', params: { speed_kmh: 30 } }, { group: 'prop', kind: 'no_parking_zone', params: {} }] } },
  evidence_levels: [],
};

describe('plan store', () => {
  it('adds with defaults, updates, removes, and undoes / redoes whole steps', () => {
    const s = new PlanStore(emptyPlan('Town05'));
    const a = s.add('rule', speed, 10.123, 20);
    expect(a).toMatchObject({ id: 'i1', x: 10.12, y: 20, params: { speed_kmh: 50, scope: 'lane' } });
    s.update(a.id, { params: { speed_kmh: 30, scope: 'road' } });
    expect(s.find('i1')!.item.params).toEqual({ speed_kmh: 30, scope: 'road' });
    s.add('prop', zone, 0, 0);
    expect(countItems(s.doc)).toBe(2);
    s.undo();
    expect(countItems(s.doc)).toBe(1);
    s.redo();
    expect(countItems(s.doc)).toBe(2);
    s.undo(); s.undo(); s.undo();
    expect(countItems(s.doc)).toBe(0);
    expect(s.canUndo).toBe(false);
    s.redo();
    expect(s.canRedo).toBe(true);
    s.add('rule', speed, 1, 1); // a new change drops the redo history
    expect(s.canRedo).toBe(false);
    expect(s.newId()).toBe('i4'); // ids are never reused after an undo
  });

  it('a template is one undo step; dirty follows the saved document', () => {
    const s = new PlanStore(emptyPlan('Town05'));
    s.setMeta({ name: 'p' });
    const made = s.addTemplate(cat, 't1', 5, 6, 'r1_s0_l-1');
    expect(made.map((m) => m.kind)).toEqual(['speed_limit', 'no_parking_zone']);
    expect(made[0].params).toMatchObject({ speed_kmh: 30, scope: 'lane' });
    expect(made[0].target).toEqual({ lane_id: 'r1_s0_l-1' });
    expect(s.dirty).toBe(true);
    s.markSaved('plan-1', 1);
    expect(s.dirty).toBe(false);
    s.remove(made[0].id);
    expect(s.dirty).toBe(true);
    s.undo();
    expect(s.dirty).toBe(false);
    s.undo(); // the template undoes as one step
    expect(countItems(s.doc)).toBe(0);
  });

  it('load replaces the document and keeps ids unique', () => {
    const s = new PlanStore(emptyPlan('Town05'));
    s.load({ ...emptyPlan('Town05', null, 'x'), rules: [{ id: 'i7', kind: 'speed_limit', x: 0, y: 0 }] }, 'plan-9', 3);
    expect(s.planId).toBe('plan-9');
    expect(s.version).toBe(3);
    expect(s.newId()).toBe('i8');
    expect(s.dirty).toBe(false);
    expect(s.canUndo).toBe(false);
  });

  it('parameter checks and descriptions', () => {
    expect(paramProblems(speed, { speed_kmh: 500 })).toHaveLength(1);
    expect(paramProblems(speed, { speed_kmh: 30, scope: 'wide' })).toHaveLength(1);
    expect(paramProblems(speed, { speed_kmh: 30, scope: 'road' })).toEqual([]);
    expect(describeItem({ id: 'i1', kind: 'speed_limit', x: 0, y: 0, params: { speed_kmh: 30 } }, speed)).toBe('Speed limit: limit 30 km/h, applies to lane');
    expect(defaultsFor(zone)).toEqual({ length_m: 20, width_m: 4, grace_s: 30 });
  });

  it('footprints follow the heading', () => {
    const r = rectPolygon(0, 0, 90, 10, 2);
    const ys = r.map((p) => p[1]), xs = r.map((p) => p[0]);
    expect(Math.max(...ys) - Math.min(...ys)).toBeCloseTo(10);
    expect(Math.max(...xs) - Math.min(...xs)).toBeCloseTo(2);
    expect(footprint({ id: 'a', kind: 'no_parking_zone', x: 5, y: 5, yaw_deg: 0 }, zone)).toHaveLength(4);
    expect(footprint({ id: 'a', kind: 'speed_limit', x: 5, y: 5 }, speed)).toBeNull(); // a lane rule has no footprint
    expect(footprint({ id: 'a', kind: 'traffic_signal', x: 5, y: 5 }, signal)).toBeNull();
  });

  it('applies an alternative to a copy', () => {
    const doc = { ...emptyPlan('Town05', null, 'p'), rules: [{ id: 'i1', kind: 'speed_limit', x: 1, y: 2, params: { speed_kmh: 30 } }], props: [{ id: 'i2', kind: 'traffic_signal', x: 3, y: 4 }] };
    const down = applyAlternative(doc, { for_item: 'i1', title: '10 less', test: { param_delta: { speed_kmh: -10 } } }, cat)!;
    expect(down.rules[0].params!.speed_kmh).toBe(20);
    expect(doc.rules[0].params!.speed_kmh).toBe(30);
    expect(down.name).toBe('p / 10 less');
    const set = applyAlternative(doc, { for_item: 'i1', title: 'road', test: { param_set: { scope: 'road' } } }, cat)!;
    expect(set.rules[0].params).toMatchObject({ speed_kmh: 30, scope: 'road' });
    const add = applyAlternative(doc, { for_item: 'i2', title: 'zone', test: { add: { group: 'prop', kind: 'no_parking_zone', params: { grace_s: 5 } } } }, cat)!;
    expect(add.props).toHaveLength(2);
    expect(add.props[1]).toMatchObject({ kind: 'no_parking_zone', x: 3, y: 4, params: { grace_s: 5, length_m: 20 } });
    expect(applyAlternative(doc, { for_item: 'i1', title: 'x', test: null }, cat)).toBeNull();
    expect(applyAlternative(doc, { for_item: 'nope', title: 'x', test: { param_set: {} } }, cat)).toBeNull();
  });
});

const analysis = (over: Partial<PlanAnalysis> = {}): PlanAnalysis => ({
  validation: { ok: true, errors: [], warnings: [], conflicts: [], infos: [] },
  compiled: { evaluated: [], not_evaluated: [], changes: { lane_overrides: [], zones: [] } },
  risks: [], alternatives: [], pending_metrics: ['queue length after'], evidence: { level: 'preliminary', why: 'x' }, ...over,
});

describe('recommendation panel html', () => {
  it('escapes text and labels the evidence level', () => {
    expect(esc('<b>"x"</b>')).toBe('&lt;b&gt;&quot;x&quot;&lt;/b&gt;');
    expect(evidenceBadge('replay-validated')).toContain('rb-replay-validated');
    expect(evidenceBadge('insufficient data')).toContain('rb-insufficient-data');
  });

  it('lists validation problems by level and says when there are none', () => {
    const v = { ok: false, errors: [{ level: 'error' as const, item_id: 'i1', code: 'c', message: 'bad <value>' }], warnings: [], conflicts: [{ level: 'conflict' as const, item_id: 'i2', code: 'c', message: 'clash' }], infos: [] };
    const html = messagesHtml(v);
    expect(html).toContain('m-err');
    expect(html).toContain('m-conf');
    expect(html).toContain('bad &lt;value&gt;');
    expect(messagesHtml(analysis().validation)).toContain('No problems');
  });

  it('draws before / after bars and the totals', () => {
    const svg = beforeAfterChart({ speeding: 2, wrong_way: 1 }, { speeding: 5, wrong_way: 0 });
    expect(svg).toContain('<svg');
    expect(svg).toContain('speeding');
    expect(svg).toContain('class="cw"');
    expect(svg).toContain('class="cg"');
    expect(beforeAfterChart({}, {})).toContain('No violations');
    const html = replayHtml(analysis({ replay: { label: 'rule replay (measured)', note: 'n', scope: 's', rows: 10, tracks: 2, seconds: 1, baseline: { counted: 3, by_type: { a: 3 } }, modified: { counted: 5, by_type: { a: 5 } }, added: [], removed: [] } }));
    expect(html).toContain('<b>3</b>');
    expect(html).toContain('<b>5</b>');
    expect(html).toContain('+2');
    expect(html).toContain('worse');
    expect(replayHtml(analysis())).toContain('no before / after');
  });

  it('shows best and worst cases only with a replay, and lets the planner fly to events', () => {
    expect(bestWorstHtml(analysis())).toContain('Available once');
    const ev = { condition: 'E1', type: 'speeding', track_ids: [1], flag_s: 12.4, x: 3, y: 4 };
    const html = bestWorstHtml(analysis({ best_case: { label: '', count: 1, removed: [ev], note: 'n' }, worst_case: { label: '', count: 0, added: [], note: 'n' } }));
    expect(html).toContain('data-x="3"');
    expect(html).toContain('Best case');
    expect(html).toContain('Worst case');
  });

  it('observed metrics are labelled as observed, alternatives offer a test only when there is a patch', () => {
    const o = { label: 'observed on the recorded traffic (before the change)', centre: [0, 0] as [number, number], radius_m: 60, duration_s: 100, vehicles: 12, samples: 99, mean_speed_kmh: 20.46, p85_speed_kmh: 30, density_veh_per_km2: 500, flow_veh_per_h: 1200,
      queue: { max_m: 40, mean_m: 10 }, travel_time: { vehicles: 5, mean_s: 30, p85_s: 50 }, conflicts_ttc: { count: 2 } };
    const html = observedHtml(o);
    expect(html).toContain('observed on the recorded traffic');
    expect(html).toContain('20.5 km/h');
    expect(html).toContain('(5 vehicles)');
    const alts = alternativesHtml([
      { for_item: 'i1', kind: 'speed_limit', title: 'A', benefit: 'b', risk: 'r', tradeoff: 't', test: { param_delta: { speed_kmh: -10 } }, evidence: 'preliminary', evidence_note: 'n' },
      { for_item: 'i1', kind: 'speed_limit', title: 'B', benefit: 'b', risk: 'r', tradeoff: 't', test: null, evidence: 'preliminary', evidence_note: 'n' }]);
    expect(alts).toContain('data-alt="0"');
    expect(alts).toContain('Needs a CARLA run');
    expect(alts).not.toContain('data-alt="1"');
  });

  it('compares scenarios on the same traffic and marks the fewest violations', () => {
    expect(compareHtml([])).toContain('Test an alternative');
    const html = compareHtml([
      { name: 'plan', evidence: 'replay-validated', before: 17, after: 46, added: 29, removed: 0, current: false },
      { name: 'plan / 10 more', evidence: 'replay-validated', before: 17, after: 20, added: 3, removed: 0, current: true }]);
    expect(html).toContain('<b class="better">20</b>');
    expect(html).toContain('not ranked here');
  });
});

describe('virtual simulation card', () => {
  it('shows baseline and plan with intervals, the verdict, play buttons and the model limits', async () => {
    const { virtualHtml } = await import('./recoView');
    const m = (label: string, b: number, p: number, d: number, dir: 'better' | 'worse' | 'none', verdict: string) => ({
      label, unit: 'km/h', better: 1, baseline: { mean: b, ci95: 1.2, n: 5 }, plan: { mean: p, ci95: 0.8, n: 5 }, diff: d, verdict, direction: dir,
    });
    const html = virtualHtml({
      label: 'virtual simulation (simplified model, not CARLA)', seeds: 5, duration_s: 120, warmup_s: 20, region: { centre: [0, 0], radius_m: 120 },
      notes: ['No lane changes'], metrics: { mean_speed_kmh: m('Average vehicle speed', 30, 22, -8, 'worse', 'different'), queue_max_m: m('Queue length (max)', 5, 5.5, 0.5, 'none', 'no difference detected') },
      trajectories: { baseline: {}, plan: {} },
    });
    expect(html).toContain('30 <span class="muted">± 1.2</span>');
    expect(html).toContain('22 <span class="muted">± 0.8</span>');
    expect(html).toContain('class="worse">-8');
    expect(html).toContain('no difference detected');
    expect(html).toContain('data-play="baseline"');
    expect(html).toContain('data-play="plan"');
    expect(html).toContain('not CARLA');
    expect(html).toContain('No lane changes');
    expect(html).toContain('5 runs per variant');
  });
});

describe('custom traffic settings', () => {
  const schema = {
    sections: [
      { id: 'run', title: 'Run', help: '', fields: [{ key: 'seed', label: 'Random seed', type: 'number' as const, default: 0, min: 0, max: 999 }] },
      { id: 'background', title: 'Background traffic', help: '', fields: [
        { key: 'background.density_per_km', label: 'Density', type: 'number' as const, default: 7, min: 0, max: 40, unit: 'veh per km of lane' },
        { key: 'background.demand_profile', label: 'Demand over the run', type: 'enum' as const, default: 'constant', options: ['constant', 'ramp_up'] }] },
    ],
    defaults: { preset: 'moderate', seed: 0, background: { enabled: true, density_per_km: 7, demand_profile: 'constant', mix: { car: 80, truck: 20 }, behaviour_mix: { normal: 100 } } },
    presets: {
      heavy: { label: 'Heavy traffic', help: '14 per km', config: { preset: 'heavy', seed: 0, background: { enabled: true, density_per_km: 14, demand_profile: 'constant', mix: { car: 70, truck: 30 }, behaviour_mix: { normal: 100 } } } },
    },
    scenario_presets: { crash: { label: 'Crash at rush hour', help: 'blocks a lane', traffic: { preset: 'heavy' }, items: [{ kind: 'speed_limit', params: { speed_kmh: 30 } }, { kind: 'no_parking_zone', params: { grace_s: 5 } }] } },
    profiles: { constant: { label: 'Constant', points: [[0, 1], [1, 1]] as [number, number][] }, ramp_up: { label: 'Building up', points: [[0, 0.3], [1, 1.6]] as [number, number][] } },
    source_mixes: {}, behaviours: {}, vehicle_types: ['car', 'truck'],
  };

  it('normalises like the backend: defaults, then the preset, then explicit fields', async () => {
    const t = await import('./trafficUi');
    expect(t.normalizeTraffic(schema, null).background.density_per_km).toBe(7);
    expect(t.normalizeTraffic(schema, { preset: 'heavy' }).background.density_per_km).toBe(14);
    const edited = t.normalizeTraffic(schema, { preset: 'heavy', background: { density_per_km: 3 } });
    expect(edited.background.density_per_km).toBe(3);
    expect(edited.background.mix.truck).toBe(30); // the rest of the preset stays
    expect(t.getPath(edited, 'background.mix.truck')).toBe(30);
    const set = t.setPath(edited, 'background.mix.truck', 5);
    expect(set.background.mix.truck).toBe(5);
    expect(edited.background.mix.truck).toBe(30); // a copy
  });

  it('knows when the settings are the defaults and checks ranges', async () => {
    const t = await import('./trafficUi');
    expect(t.trafficIsDefault(schema, null)).toBe(true);
    expect(t.trafficIsDefault(schema, { preset: 'moderate' })).toBe(true);
    expect(t.trafficIsDefault(schema, { preset: 'heavy' })).toBe(false);
    expect(t.trafficIsDefault(schema, { background: { density_per_km: 8 } })).toBe(false);
    expect(t.trafficProblems(schema, { background: { density_per_km: 99 } })[0]).toContain('Density: 99 is outside 0 to 40');
    expect(t.trafficProblems(schema, { background: { demand_profile: 'tsunami' } })).toHaveLength(1);
    expect(t.trafficProblems(schema, { background: { mix: { car: 0, truck: 0 } } })[0]).toContain('Vehicle mix');
    expect(t.trafficProblems(schema, { preset: 'heavy' })).toEqual([]);
  });

  it('draws the demand profile, the mix bars and the Traffic tab with its presets and scenarios', async () => {
    const t = await import('./trafficUi');
    expect(t.profileSvg([[0, 0.3], [1, 1.6]])).toContain('<polyline');
    const bar = t.stackBar({ car: 80, truck: 20, bus: 0 }, t.TYPE_COLOR);
    expect(bar).toContain('car 80 %');
    expect(bar).toContain('truck 20 %');
    expect(bar).not.toContain('bus');
    expect(t.stackBar({ car: 0 }, t.TYPE_COLOR)).toContain('no shares set');
    const html = t.trafficTabHtml(schema, { preset: 'heavy' }, null, true, true, { flows: 1, groups: 0, zones: 2, incidents: 0 });
    expect(html).toContain('data-tpreset="heavy"');
    expect(html).toContain('class="chip on"');
    expect(html).toContain('data-tscn="crash"');
    expect(html).toContain('data-tf="background.density_per_km"');
    expect(html).toContain('Heavy traffic: 14 veh/km');
    const noAnchor = t.trafficTabHtml(schema, null, null, true, false, { flows: 0, groups: 0, zones: 0, incidents: 0 });
    expect(noAnchor).toMatch(/data-tscn="crash"\s+disabled/); // a scenario needs a place on the map
    expect(noAnchor).toContain('Right-click the map');
  });

  it('a traffic scenario sets the settings and adds its items in one undo step', () => {
    const s = new PlanStore(emptyPlan('Town05'));
    const made = s.addTrafficScenario(cat, schema.scenario_presets.crash, 5, 6, 'r1_s0_l-1');
    expect(made.map((m) => m.kind)).toEqual(['speed_limit', 'no_parking_zone']);
    expect(made[1].params).toMatchObject({ grace_s: 5, length_m: 20 });
    expect(made[0].target).toEqual({ lane_id: 'r1_s0_l-1' });
    expect(s.doc.traffic).toEqual({ preset: 'heavy' });
    s.undo();
    expect(countItems(s.doc)).toBe(0);
    expect(s.doc.traffic).toBeUndefined();
    s.setTraffic({ preset: 'night' });
    expect(s.doc.traffic).toEqual({ preset: 'night' });
    s.setTraffic(null);
    expect(s.doc.traffic).toBeUndefined();
  });
});
