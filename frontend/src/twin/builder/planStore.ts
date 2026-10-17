/**
 * The plan being built (Scenario_Builder_Plan.md): items, undo / redo, templates, save state. Pure and unit tested;
 * the builder UI (PlanBuilder.ts) and the 3D markers read from it.
 *
 * Undo / redo keep whole-document snapshots (a plan is a few dozen small items), so any action, including a template
 * or a restored version, undoes in one step. `dirty` compares with the last saved document.
 */
import type { PlanCatalogue, PlanDoc, PlanGroup, PlanItem, PlanKind } from '../../api/types';

export const GROUP_KEY: Record<PlanGroup, 'props' | 'rules' | 'scenarios'> = { prop: 'props', rule: 'rules', scenario: 'scenarios' };
export const GROUPS: PlanGroup[] = ['prop', 'rule', 'scenario'];
const HISTORY_MAX = 100;

export function emptyPlan(scene: string, sessionId: string | null = null, name = ''): PlanDoc {
  return { name, description: '', scene, session_id: sessionId, objectives: [], props: [], rules: [], scenarios: [] };
}

export const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

/** Parameter values of a kind with the catalogue defaults filled in. */
export function defaultsFor(kind: PlanKind): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of kind.params) if (p.default !== null && p.default !== undefined) out[p.key] = p.default;
  return out;
}

export function allItems(plan: PlanDoc): { group: PlanGroup; item: PlanItem }[] {
  return GROUPS.flatMap((g) => plan[GROUP_KEY[g]].map((item) => ({ group: g, item })));
}

export function countItems(plan: PlanDoc): number {
  return GROUPS.reduce((n, g) => n + plan[GROUP_KEY[g]].length, 0);
}

/** Short text of an item's parameters for lists: `Speed limit 30 km/h, applies to section`. */
export function describeItem(item: PlanItem, kind: PlanKind | undefined): string {
  if (!kind) return item.kind;
  const p = { ...defaultsFor(kind), ...(item.params ?? {}) };
  const bits = kind.params
    .filter((q) => p[q.key] !== undefined && p[q.key] !== null && p[q.key] !== '')
    .slice(0, 3)
    .map((q) => `${q.label.toLowerCase()} ${typeof p[q.key] === 'boolean' ? (p[q.key] ? 'yes' : 'no') : p[q.key]}${q.unit ? ` ${q.unit}` : ''}`);
  return `${kind.label}${bits.length ? ': ' + bits.join(', ') : ''}`;
}

/** Problems the form can show before the server is asked (the server validates again). */
export function paramProblems(kind: PlanKind, params: Record<string, unknown>): string[] {
  const out: string[] = [];
  for (const q of kind.params) {
    const v = params[q.key];
    if (v === undefined || v === null || v === '') continue;
    if (q.type === 'number') {
      if (typeof v !== 'number' || !Number.isFinite(v)) out.push(`${q.label}: enter a number`);
      else if ((q.min !== undefined && v < q.min) || (q.max !== undefined && v > q.max))
        out.push(`${q.label}: ${v} is outside ${q.min ?? '-'} to ${q.max ?? '-'}${q.unit ? ' ' + q.unit : ''}`);
    } else if (q.type === 'enum' && q.options && !q.options.includes(String(v))) out.push(`${q.label}: pick one of the options`);
  }
  return out;
}

export class PlanStore {
  doc: PlanDoc;
  planId: string | null = null;
  version = 0;
  private saved: string;
  private past: string[] = [];
  private future: string[] = [];
  private nextId = 1;
  private listeners = new Set<() => void>();

  constructor(doc: PlanDoc) {
    this.doc = doc;
    this.saved = JSON.stringify(doc);
    this.reindex();
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit() {
    this.listeners.forEach((f) => f());
  }

  private reindex() {
    for (const { item } of allItems(this.doc)) {
      const m = /^i(\d+)$/.exec(item.id);
      if (m) this.nextId = Math.max(this.nextId, Number(m[1]) + 1);
    }
  }

  get dirty(): boolean {
    return JSON.stringify(this.doc) !== this.saved;
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  /** Run a change as one undo step. */
  change(fn: (doc: PlanDoc) => void) {
    const before = JSON.stringify(this.doc);
    fn(this.doc);
    if (JSON.stringify(this.doc) === before) return;
    this.past.push(before);
    if (this.past.length > HISTORY_MAX) this.past.shift();
    this.future = [];
    this.emit();
  }

  undo() {
    const prev = this.past.pop();
    if (prev === undefined) return;
    this.future.push(JSON.stringify(this.doc));
    this.doc = JSON.parse(prev) as PlanDoc;
    this.emit();
  }

  redo() {
    const next = this.future.pop();
    if (next === undefined) return;
    this.past.push(JSON.stringify(this.doc));
    this.doc = JSON.parse(next) as PlanDoc;
    this.emit();
  }

  newId(): string {
    return `i${this.nextId++}`;
  }

  find(id: string): { group: PlanGroup; item: PlanItem } | undefined {
    return allItems(this.doc).find((e) => e.item.id === id);
  }

  add(group: PlanGroup, kind: PlanKind, x: number, y: number, extra: Partial<PlanItem> = {}): PlanItem {
    const item: PlanItem = {
      id: this.newId(), kind: kind.key, x: round(x), y: round(y), yaw_deg: 0, params: defaultsFor(kind), ...extra,
    };
    this.change((d) => d[GROUP_KEY[group]].push(item));
    return item;
  }

  /** Several items of one kind in one undo step (a signal on every approach of a junction). */
  addMany(group: PlanGroup, kind: PlanKind, at: { x: number; y: number; extra?: Partial<PlanItem> }[]): PlanItem[] {
    const made = at.map(({ x, y, extra }) => ({ id: this.newId(), kind: kind.key, x: round(x), y: round(y), yaw_deg: 0, params: defaultsFor(kind), ...extra }) as PlanItem);
    if (made.length) this.change((d) => d[GROUP_KEY[group]].push(...made));
    return made;
  }

  update(id: string, patch: Partial<PlanItem>) {
    this.change((d) => {
      const e = allItems(d).find((q) => q.item.id === id);
      if (e) Object.assign(e.item, patch, patch.params ? { params: { ...patch.params } } : {});
    });
  }

  remove(id: string) {
    this.change((d) => {
      for (const g of GROUPS) d[GROUP_KEY[g]] = d[GROUP_KEY[g]].filter((i) => i.id !== id);
    });
  }

  /** Move every item by id to a new position (the planner picked a new place). */
  move(id: string, x: number, y: number) {
    this.update(id, { x: round(x), y: round(y) });
  }

  /** A ready-made traffic scenario: its traffic settings plus its items at a position, as one undo step. */
  addTrafficScenario(cat: PlanCatalogue, sp: { traffic: Record<string, unknown>; items: { kind: string; params: Record<string, unknown>; schedule?: PlanItem['schedule'] }[] },
                     x: number, y: number, laneId?: string): PlanItem[] {
    const made: PlanItem[] = [];
    this.change((d) => {
      d.traffic = JSON.parse(JSON.stringify(sp.traffic));
      for (const it of sp.items) {
        const kind = cat.kinds[it.kind];
        if (!kind) continue;
        const item: PlanItem = {
          id: this.newId(), kind: kind.key, x: round(x), y: round(y), yaw_deg: 0, params: { ...defaultsFor(kind), ...it.params },
          ...(it.schedule ? { schedule: { ...it.schedule } } : {}), ...(laneId ? { target: { lane_id: laneId } } : {}),
        };
        d[GROUP_KEY[kind.group]].push(item);
        made.push(item);
      }
    });
    return made;
  }

  /** The custom traffic configuration (null: back to the defaults). */
  setTraffic(cfg: Record<string, unknown> | null) {
    this.change((d) => {
      if (cfg === null) delete d.traffic;
      else d.traffic = cfg;
    });
  }

  setMeta(patch: Partial<Pick<PlanDoc, 'name' | 'description' | 'session_id' | 'objectives'>>) {
    this.change((d) => Object.assign(d, patch));
  }

  /** Items of a template at a position; params of the template override the catalogue defaults. */
  addTemplate(cat: PlanCatalogue, key: string, x: number, y: number, laneId?: string): PlanItem[] {
    const t = cat.templates[key];
    if (!t) return [];
    const made: PlanItem[] = [];
    this.change((d) => {
      for (const it of t.items) {
        const kind = cat.kinds[it.kind];
        if (!kind) continue;
        const item: PlanItem = {
          id: this.newId(), kind: kind.key, x: round(x), y: round(y), yaw_deg: 0,
          params: { ...defaultsFor(kind), ...it.params }, ...(laneId ? { target: { lane_id: laneId } } : {}),
        };
        d[GROUP_KEY[it.group]].push(item);
        made.push(item);
      }
    });
    return made;
  }

  /** The document as saved on the server (a new plan, a loaded version, or after a save). */
  markSaved(planId: string, version: number, doc?: PlanDoc) {
    if (doc) this.doc = clone(doc);
    this.planId = planId;
    this.version = version;
    this.saved = JSON.stringify(this.doc);
    this.reindex();
    this.emit();
  }

  /** Replace the document (open a plan / start a new one); clears the history. */
  load(doc: PlanDoc, planId: string | null = null, version = 0) {
    this.doc = clone(doc);
    this.planId = planId;
    this.version = version;
    this.saved = planId ? JSON.stringify(this.doc) : '';
    this.past = [];
    this.future = [];
    this.nextId = 1;
    this.reindex();
    this.emit();
  }
}

const round = (v: number) => Math.round(v * 100) / 100;

/** Rectangle centred on (x, y), `length` along the heading yaw_deg (CARLA yaw: 0 = +x, 90 = +y); same as plans.rect_polygon. */
export function rectPolygon(x: number, y: number, yawDeg: number, length: number, width: number): [number, number][] {
  const h = (yawDeg * Math.PI) / 180;
  const ux = Math.cos(h), uy = Math.sin(h), vx = -Math.sin(h), vy = Math.cos(h);
  const hl = length / 2, hw = width / 2;
  return ([[-1, -1], [1, -1], [1, 1], [-1, 1]] as const).map(([sx, sy]) => [x + sx * hl * ux + sy * hw * vx, y + sx * hl * uy + sy * hw * vy] as [number, number]);
}

/** The footprint of an item that has a length / width, else null (a point item). */
export function footprint(item: PlanItem, kind: PlanKind | undefined): [number, number][] | null {
  if (!kind || kind.target === 'lane') return null;
  const p = { ...defaultsFor(kind), ...(item.params ?? {}) };
  const l = Number(p.length_m), w = Number(p.width_m);
  return l > 0 && w > 0 ? rectPolygon(item.x, item.y, item.yaw_deg ?? 0, l, w) : null;
}

/** A copy of the plan with an alternative's test patch applied (backend `alternatives_for`), or null when it is not testable. */
export function applyAlternative(doc: PlanDoc, alt: { for_item: string; title: string; test: Record<string, unknown> | null }, cat: PlanCatalogue): PlanDoc | null {
  const t = alt.test as { add?: { group: PlanGroup; kind: string; params?: Record<string, unknown> }; param_set?: Record<string, unknown>; param_delta?: Record<string, number> } | null;
  if (!t) return null;
  const out = clone(doc);
  out.name = `${doc.name || 'plan'} / ${alt.title}`;
  const src = allItems(out).find((e) => e.item.id === alt.for_item)?.item;
  if (!src) return null;
  if (t.add) {
    const kind = cat.kinds[t.add.kind];
    if (!kind) return null;
    const id = `alt${allItems(out).length + 1}`;
    out[GROUP_KEY[t.add.group]].push({ id, kind: kind.key, x: src.x, y: src.y, yaw_deg: src.yaw_deg ?? 0, params: { ...defaultsFor(kind), ...(t.add.params ?? {}) }, ...(src.target ? { target: { ...src.target } } : {}) });
    return out;
  }
  const kind = cat.kinds[src.kind];
  const params = { ...(kind ? defaultsFor(kind) : {}), ...(src.params ?? {}) };
  for (const [k, v] of Object.entries(t.param_set ?? {})) params[k] = v;
  for (const [k, d] of Object.entries(t.param_delta ?? {})) params[k] = Number(params[k] ?? 0) + d;
  src.params = params;
  return out;
}
