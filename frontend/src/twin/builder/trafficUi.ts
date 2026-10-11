/**
 * Custom traffic configuration in the builder (Traffic tab): pure helpers and HTML, unit tested. The form is drawn from the
 * backend's schema (GET /api/plans/traffic/schema, ml/planning/traffic_config.py) so the fields, ranges and presets live in one
 * place. The config the plan stores is the full set of settings with preset "custom" once a field was edited.
 */
import type { TrafficResult, TrafficSchema } from '../../api/types';
import { esc } from './recoView';

export type Cfg = Record<string, any>;

export const TYPE_COLOR: Record<string, string> = { car: '#4c6ef5', van: '#15aabf', truck: '#f08c00', bus: '#2f9e44', motorcycle: '#e64980' };
export const BEHAVIOUR_COLOR: Record<string, string> = { cautious: '#2f9e44', normal: '#4c6ef5', aggressive: '#fa5252' };

export const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

export function deepMerge(base: Cfg, patch: Cfg | null | undefined): Cfg {
  const out = clone(base);
  for (const [k, v] of Object.entries(patch ?? {})) {
    if (v !== null && typeof v === 'object' && !Array.isArray(v) && typeof out[k] === 'object' && out[k] !== null) out[k] = deepMerge(out[k], v as Cfg);
    else if (v !== undefined && v !== null) out[k] = clone(v);
  }
  return out;
}

/** Defaults, then the named preset, then the explicit fields: the same as the backend's `normalize`. */
export function normalizeTraffic(schema: TrafficSchema, cfg: Cfg | null | undefined): Cfg {
  const preset = cfg?.preset ? schema.presets[String(cfg.preset)] : undefined;
  const base = preset ? clone(preset.config) : clone(schema.defaults);
  const { preset: _p, ...rest } = cfg ?? {};
  const out = deepMerge(base, rest);
  out.preset = cfg?.preset ?? schema.defaults.preset;
  return out;
}

export function getPath(o: Cfg, key: string): any {
  return key.split('.').reduce<any>((cur, k) => (cur === undefined || cur === null ? undefined : cur[k]), o);
}

/** A copy of `o` with the dotted key set. */
export function setPath(o: Cfg, key: string, value: unknown): Cfg {
  const out = clone(o);
  const parts = key.split('.');
  let cur = out;
  for (const k of parts.slice(0, -1)) cur = cur[k] ??= {};
  cur[parts[parts.length - 1]] = value;
  return out;
}

export function trafficIsDefault(schema: TrafficSchema, cfg: Cfg | null | undefined): boolean {
  if (!cfg) return true;
  const a = normalizeTraffic(schema, cfg), b = normalizeTraffic(schema, {});
  a.preset = b.preset = '';
  return JSON.stringify(sortKeys(a)) === JSON.stringify(sortKeys(b));
}

function sortKeys(x: any): any {
  if (Array.isArray(x) || x === null || typeof x !== 'object') return x;
  return Object.fromEntries(Object.keys(x).sort().map((k) => [k, sortKeys(x[k])]));
}

/** Problems with a config before the server is asked (same ranges as the backend). */
export function trafficProblems(schema: TrafficSchema, cfg: Cfg | null | undefined): string[] {
  const c = normalizeTraffic(schema, cfg);
  const out: string[] = [];
  for (const sec of schema.sections)
    for (const f of sec.fields) {
      const v = getPath(c, f.key);
      if (f.type === 'number') {
        if (typeof v !== 'number' || !Number.isFinite(v)) out.push(`${f.label}: enter a number`);
        else if ((f.min !== undefined && v < f.min) || (f.max !== undefined && v > f.max)) out.push(`${f.label}: ${v} is outside ${f.min} to ${f.max}${f.unit ? ' ' + f.unit : ''}`);
      } else if (f.type === 'enum' && f.options && !f.options.includes(String(v))) out.push(`${f.label}: pick one of the options`);
    }
  if (c.background.enabled) {
    if (Object.values(c.background.mix as Record<string, number>).reduce((a, b) => a + b, 0) <= 0) out.push('Vehicle mix: at least one type needs a share above 0');
    if (Object.values(c.background.behaviour_mix as Record<string, number>).reduce((a, b) => a + b, 0) <= 0) out.push('Driver behaviour: at least one kind of driver needs a share above 0');
  }
  return out;
}

export function describeTraffic(schema: TrafficSchema, cfg: Cfg | null | undefined): string {
  const c = normalizeTraffic(schema, cfg);
  const bg = c.background;
  if (!bg.enabled) return 'No background traffic';
  const preset = schema.presets[String(c.preset)]?.label;
  return `${preset ?? 'Custom'}: ${bg.density_per_km} veh/km, ${(schema.profiles[bg.demand_profile]?.label ?? bg.demand_profile).toLowerCase()} demand, seed ${c.seed}`;
}

/** Demand over the run as a small line chart. */
export function profileSvg(points: [number, number][]): string {
  const w = 200, h = 46, max = Math.max(2, ...points.map((p) => p[1]));
  const xy = points.map(([x, y]) => `${(x * (w - 6) + 3).toFixed(1)},${(h - 4 - (y / max) * (h - 10)).toFixed(1)}`);
  const base = (h - 4 - (1 / max) * (h - 10)).toFixed(1);
  return `<svg viewBox="0 0 ${w} ${h}" class="profile" role="img" aria-label="demand over the run"><line x1="3" x2="${w - 3}" y1="${base}" y2="${base}" class="p1"/>
    <polyline points="${xy.join(' ')}" class="pl"/></svg>`;
}

/** A stacked share bar with a legend; shares are normalised. */
export function stackBar(weights: Record<string, number>, colors: Record<string, string>): string {
  const total = Object.values(weights).reduce((a, b) => a + b, 0);
  if (total <= 0) return '<div class="stack empty">no shares set</div>';
  const parts = Object.entries(weights).filter(([, v]) => v > 0);
  return `<div class="stack">${parts.map(([k, v]) => `<i style="width:${((v / total) * 100).toFixed(1)}%;background:${colors[k] ?? '#868e96'}" title="${esc(k)} ${((v / total) * 100).toFixed(0)} %"></i>`).join('')}</div>
    <div class="stackleg">${parts.map(([k, v]) => `<span><i style="background:${colors[k] ?? '#868e96'}"></i>${esc(k.replace(/_/g, ' '))} ${((v / total) * 100).toFixed(0)} %</span>`).join('')}</div>`;
}

function fieldHtml(f: TrafficSchema['sections'][number]['fields'][number], v: unknown, schema: TrafficSchema): string {
  const tip = f.help ? ` title="${esc(f.help)}"` : '';
  if (f.type === 'bool') return `<label class="chk"${tip}><input type="checkbox" data-tf="${f.key}" ${v ? 'checked' : ''}/> ${esc(f.label)}</label>`;
  if (f.type === 'enum') {
    return `<label${tip}>${esc(f.label)}<select data-tf="${f.key}">${(f.options ?? []).map((o) => `<option value="${esc(o)}" ${String(v) === o ? 'selected' : ''}>${esc(schema.profiles[o]?.label ?? o.replace(/_/g, ' '))}</option>`).join('')}</select></label>`;
  }
  const range = f.min !== undefined && f.max !== undefined;
  return `<label${tip}>${esc(f.label)}${f.unit ? ` <span class="muted">(${esc(f.unit)})</span>` : ''}
    <span class="num"><input type="number" data-tf="${f.key}" value="${v ?? ''}" ${f.min !== undefined ? `min="${f.min}"` : ''} ${f.max !== undefined ? `max="${f.max}"` : ''} step="${f.step ?? 'any'}"/>
    ${range ? `<input type="range" data-tfr="${f.key}" min="${f.min}" max="${f.max}" step="${f.step ?? 'any'}" value="${v ?? f.min}" tabindex="-1" aria-hidden="true"/>` : ''}</span></label>`;
}

export const TRAFFIC_ITEM_KINDS = ['traffic_flow', 'spawn_vehicles', 'traffic_density', 'peak_hour', 'demand_surge', 'special_event', 'congestion_jam', 'accident', 'breakdown', 'emergency_vehicle', 'weather'];

/** The Traffic tab: presets, the schema's sections with live visuals, quick adds, the last run, export / import. */
export function trafficTabHtml(schema: TrafficSchema, cfg: Cfg | null | undefined, last: TrafficResult | null, canEdit: boolean, hasAnchor: boolean, counts: { flows: number; groups: number; zones: number; incidents: number }): string {
  const c = normalizeTraffic(schema, cfg);
  const bg = c.background;
  const presets = Object.entries(schema.presets).map(([k, p]) => `<button class="chip ${c.preset === k ? 'on' : ''}" data-tpreset="${esc(k)}" title="${esc(p.help)}" ${canEdit ? '' : 'disabled'}>${esc(p.label)}</button>`).join('');
  const scenarios = Object.entries(schema.scenario_presets ?? {}).map(([k, sp]) => `<button class="chip scn" data-tscn="${esc(k)}" ${canEdit && hasAnchor ? '' : 'disabled'}
      title="${esc(sp.help)} Adds: ${esc(sp.items.map((i) => i.kind.replace(/_/g, ' ')).join(', '))}">${esc(sp.label)}</button>`).join('');
  const sections = schema.sections.map((sec) => {
    const visual = sec.id === 'mix' ? stackBar(bg.mix, TYPE_COLOR)
      : sec.id === 'drivers' ? stackBar(bg.behaviour_mix, BEHAVIOUR_COLOR)
      : sec.id === 'background' ? `<div class="profwrap">${profileSvg(schema.profiles[bg.demand_profile]?.points ?? [[0, 1], [1, 1]])}<span class="muted">demand over the run</span></div>` : '';
    return `<details class="tsec" ${sec.id === 'background' ? 'open' : ''}><summary><b>${esc(sec.title)}</b> <span class="muted">${esc(sec.help)}</span></summary>${visual}
      <div class="grid2 tgrid ${canEdit ? '' : 'ro'}">${sec.fields.map((f) => fieldHtml(f, getPath(c, f.key), schema)).join('')}</div></details>`;
  }).join('');
  const run = last ? `<div class="lastrun"><b>Last run</b> <span class="muted">${esc(last.description ?? '')}</span>
      <div class="kv"><span>Vehicles</span><b>${last.vehicles}</b><span>Mean speed</span><b>${last.stats?.mean_speed_kmh ?? '-'} km/h</b>
      <span>Stopped share</span><b>${last.stats?.stopped_share !== null && last.stats?.stopped_share !== undefined ? Math.round(last.stats.stopped_share * 100) + ' %' : '-'}</b><span>Queue (max)</span><b>${last.stats?.queue_max_m ?? '-'} m</b></div>
      ${stackBar(last.spawned ?? {}, TYPE_COLOR)}</div>` : '<p class="muted">Switch “Custom traffic” on to see the traffic and its statistics here.</p>';
  return `<div class="traffic-tab">
    <p class="muted">${esc(describeTraffic(schema, cfg))}</p>
    <h5>Traffic character <span class="muted">(presets: density, mix, drivers, demand)</span></h5>
    <div class="chips">${presets}</div>
    <h5>Traffic scenarios <span class="muted">(settings + sources, groups, zones or incidents at the builder location)</span></h5>
    <div class="chips">${scenarios}</div>
    ${hasAnchor ? '' : '<p class="muted">Right-click the map and choose Add Custom Rules to place a scenario.</p>'}
    ${sections}
    <details class="tsec" open><summary><b>Sources, groups and zones</b> <span class="muted">placed on the map at the builder location</span></summary>
      <div class="qadd"><button class="mini" data-tadd="traffic_flow" ${canEdit ? '' : 'disabled'}>+ Flow source <span class="muted">(${counts.flows})</span></button>
        <button class="mini" data-tadd="spawn_vehicles" ${canEdit ? '' : 'disabled'}>+ Vehicle group <span class="muted">(${counts.groups})</span></button>
        <button class="mini" data-tadd="traffic_density" ${canEdit ? '' : 'disabled'}>+ Demand zone <span class="muted">(${counts.zones})</span></button>
        <button class="mini" data-tadd="accident" ${canEdit ? '' : 'disabled'}>+ Incident <span class="muted">(${counts.incidents})</span></button></div>
      <p class="muted">Sources emit vehicles continuously (rate, mix, platoons, destination, waypoint); groups depart once; zones raise demand and change speeds locally; incidents block lanes. Edit them in the Scenarios tab or the list below.</p></details>
    <div class="trow"><button class="mini" data-act="texport">Export settings</button><label class="mini filebtn">Import settings<input type="file" accept="application/json,.json" data-f="timport" hidden/></label>
      <button class="mini" data-act="treset" ${canEdit ? '' : 'disabled'}>Reset to defaults</button></div>
    ${run}
    <div class="formmsg" id="tmsg"></div></div>`;
}
