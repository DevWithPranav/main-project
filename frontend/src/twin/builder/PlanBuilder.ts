/**
 * The interactive scenario builder of the 3D twin (Scenario_Builder_Plan.md): a left panel to add props / rules /
 * scenarios at a map position, a right recommendation panel (validation, observed baseline, replay before / after,
 * best / worst case, risks, alternatives, comparison) and a bottom bar (playback, analysis, scenario switch).
 *
 * Plain DOM like the rest of the twin (TwinView.ts creates it with a `BuilderHost` and calls `openAt` on a right-click).
 * Nothing here invents a number: results come from the backend /api/plans/* and are labelled by where they come from.
 */
import { api } from '../../api';
import type { PlanAnalysis, PlanCatalogue, PlanDoc, PlanGroup, PlanItem, PlanKind, PlanLocate, PlanRow, PlanReportRow, PlanRun, TrafficResult, TrafficSchema, VirtualResult, VirtualSignal } from '../../api/types';
import './builder.css';
import { alternativesHtml, bestWorstHtml, compareHtml, compiledHtml, esc, evidenceBadge, messagesHtml, observedHtml, pendingHtml, replayHtml, risksHtml, virtualHtml, type CompareRow } from './recoView';
import { normalizeTraffic, setPath, trafficIsDefault, trafficProblems, trafficTabHtml, type Cfg } from './trafficUi';
import { allItems, applyAlternative, countItems, defaultsFor, describeItem, emptyPlan, footprint, paramProblems, PlanStore } from './planStore';

export interface DrawnItem {
  id: string;
  group: PlanGroup;
  kind: string;
  label: string;
  x: number;
  y: number;
  footprint: [number, number][] | null;
  selected: boolean;
  level: 'ok' | 'warning' | 'error';
}

export interface BuilderHost {
  root: HTMLElement;
  role: string;
  town(): string;
  sessionId(): string;
  flyTo(x: number, y: number): void;
  draw(items: DrawnItem[], anchor: [number, number] | null): void;
  /** play the vehicle paths of a virtual-traffic run instead of the recording (null: back to the recording) */
  showVirtual(traj: VirtualResult['trajectories']['plan'] | null, signals?: VirtualSignal[] | null): void;
  /** the map's Custom traffic switch (virtual traffic that follows the plan) */
  customTraffic: { isOn(): boolean; set(on: boolean): void };
  /** the last custom-traffic run (statistics for the Traffic tab), or null */
  trafficInfo?(): TrafficResult | null;
  /** the plan changed (the custom traffic re-runs with it when the switch is on) */
  planChanged?(): void;
  /** a PNG data URL of the 3D view (for the report), or null */
  screenshot(): string | null;
  /** playback of the recorded / live traffic (the same player as the info panel) */
  player: {
    toggle(): void;
    reset(): void;
    stop(): void;
    seek(fraction: number): void;
    setSpeed(v: number): void;
    read(): { t: number; tMin: number; tMax: number; playing: boolean };
  };
}

type Tab = PlanGroup | 'template' | 'traffic';
interface Run { key: string; name: string; analysis: PlanAnalysis; at: number; stale: boolean }

const GROUP_TITLE: Record<PlanGroup, string> = { prop: 'Props', rule: 'Rules', scenario: 'Scenarios' };
const GROUP_COLOR: Record<PlanGroup, string> = { prop: '#4c6ef5', rule: '#f08c00', scenario: '#12b886' };
const CARLA_MARK = { supported: '✓', approximate: '≈', unsupported: '✕' } as const;
const dash = (s: string) => s.replace(/_/g, ' ');

/** A progress bar block (filled by updateBars): the step label, the bar, the time elapsed. */
function progressHtml(id: string, title: string, st: { stage: string; done: number; total: number }): string {
  const frac = st.total > 0 ? Math.min(st.done / st.total, 1) : 0;
  return `<div class="pbox" data-pbar="${id}"><div class="pt">${title}</div><div class="pbar"><i class="${st.done < st.total ? 'work' : ''}" style="width:${Math.max(frac * 100, 4)}%"></i></div>
    <div class="pm"><span class="pl">${esc(st.stage)}</span><span class="pe muted"></span></div></div>`;
}

export class PlanBuilder {
  readonly store: PlanStore;
  private readonly host: BuilderHost;
  private left!: HTMLElement;
  private right!: HTMLElement;
  private bottom!: HTMLElement;
  private cat: PlanCatalogue | null = null;
  private tschema: TrafficSchema | null = null;
  private tschemaError = '';
  private opened = false;
  private anchor: { x: number; y: number; loc: PlanLocate | null } | null = null;
  private tab: Tab = 'prop';
  private editing: string | null = null;
  private selectedId: string | null = null;
  private preview: PlanAnalysis | null = null;
  private previewSeq = 0;
  private previewTimer: ReturnType<typeof setTimeout> | undefined;
  private runs: Run[] = [];
  private shownRun: string | null = null; // null = the draft's preview
  private analysing: { since: number; name: string; stage: string; done: number; total: number } | null = null;
  private virtual: { result: VirtualResult; name: string; stale: boolean } | null = null;
  private simulating: { since: number; stage: string; done: number; total: number } | null = null;
  private playing: 'baseline' | 'plan' | null = null;
  private reportOpen = false;
  private reportBusy = false;
  private reportMsg = '';
  private reports: PlanReportRow[] = [];
  private plans: PlanRow[] = [];
  private msg = '';
  private disposed = false;
  private tickTimer: ReturnType<typeof setInterval> | undefined;

  constructor(host: BuilderHost) {
    this.host = host;
    this.store = new PlanStore(emptyPlan(host.town(), host.sessionId() || null));
    this.store.subscribe(() => this.onChange());
  }

  get isOpen() {
    return this.opened;
  }

  // ---------------------------------------------------------------- mounting

  mount() {
    const mk = (tag: string, cls: string) => {
      const e = document.createElement(tag);
      e.className = cls;
      e.hidden = true;
      this.host.root.appendChild(e);
      return e;
    };
    this.left = mk('aside', 'tw-panel tw-builder');
    this.right = mk('aside', 'tw-panel tw-reco');
    this.bottom = mk('div', 'tw-bottom');
    for (const el of [this.left, this.right, this.bottom]) {
      el.addEventListener('click', (e) => void this.onClick(e));
      el.addEventListener('change', (e) => void this.onChangeEvent(e));
      el.addEventListener('input', (e) => this.onInput(e));
    }
    this.tickTimer = setInterval(() => this.updateClock(), 400);
  }

  dispose() {
    this.disposed = true;
    clearInterval(this.tickTimer);
    clearTimeout(this.previewTimer);
    this.left?.remove();
    this.right?.remove();
    this.bottom?.remove();
  }

  /** The gear next to the Custom traffic switch: open the builder on the Traffic tab (no map position needed). */
  async openTraffic() {
    this.tab = 'traffic';
    if (!this.store.planId && this.host.town() && this.store.doc.scene !== this.host.town()) this.store.doc.scene = this.host.town();
    if (!this.opened) {
      this.opened = true;
      this.host.root.classList.add('builder-open');
      for (const el of [this.left, this.right, this.bottom]) el.hidden = false;
      void this.loadPlans();
    }
    this.render();
    await this.ensureCatalogues();
    this.render();
  }

  private async ensureCatalogues() {
    // loaded separately: a missing traffic schema (an older backend) must not block the props / rules / scenarios tabs
    try {
      if (!this.cat) this.cat = await api.planCatalogue();
    } catch (e) {
      this.msg = `Could not load the catalogue: ${(e as Error).message}`;
    }
    try {
      if (!this.tschema) this.tschema = await api.trafficSchema();
    } catch (e) {
      this.tschemaError = `The traffic settings could not be loaded (${(e as Error).message}). Restart the backend so it has the newest routes.`;
    }
  }

  /** The traffic statistics changed (a custom-traffic run finished). */
  refreshTrafficInfo() {
    if (this.opened && this.tab === 'traffic') this.renderLeft();
  }

  /** Right-click "Add Custom Rules" (or "Set location here"): open the builder at a map position. */
  async openAt(x: number, y: number) {
    this.anchor = { x, y, loc: null };
    // the town is only known once the map has loaded: a new plan takes the current one (no undo step)
    if (!this.store.planId && this.host.town() && this.store.doc.scene !== this.host.town()) this.store.doc.scene = this.host.town();
    if (!this.opened) {
      this.opened = true;
      this.host.root.classList.add('builder-open');
      for (const el of [this.left, this.right, this.bottom]) el.hidden = false;
      if (!this.store.doc.session_id && this.host.sessionId()) this.store.setMeta({ session_id: this.host.sessionId() });
      void this.loadPlans();
    }
    this.render();
    await this.ensureCatalogues();
    this.render();
    try {
      const loc = await api.planLocate(this.host.town(), x, y);
      if (this.anchor && this.anchor.x === x && this.anchor.y === y) this.anchor.loc = loc;
    } catch (e) {
      this.msg = (e as Error).message;
    }
    this.render();
    this.redraw();
  }

  close() {
    this.opened = false; // the custom traffic stays on the map (its switch is in the info panel)
    this.host.root.classList.remove('builder-open');
    for (const el of [this.left, this.right, this.bottom]) el.hidden = true;
    this.host.draw([], null);
  }

  /** A plan marker was clicked in the 3D view. */
  selectItem(id: string | null) {
    this.selectedId = id;
    this.redraw();
    this.renderLeft();
    if (id) this.left.querySelector(`[data-row="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest' });
  }

  // ---------------------------------------------------------------- data

  private async loadPlans() {
    try {
      this.plans = await api.plans(this.host.town());
    } catch {
      this.plans = [];
    }
    this.renderLeft();
  }

  private kind(key: string): PlanKind | undefined {
    return this.cat?.kinds[key];
  }

  get hasItems() {
    return countItems(this.store.doc) > 0;
  }

  /** The plan the custom traffic should follow: null while it has no items (plain traffic). */
  planForTraffic(): PlanDoc | null {
    const custom = !!this.store.doc.traffic && !(this.tschema && trafficIsDefault(this.tschema, this.store.doc.traffic));
    return this.hasItems || custom ? { ...this.store.doc, name: this.store.doc.name || 'draft plan' } : null;
  }

  private onChange() {
    this.runs.forEach((r) => (r.stale = true));
    this.host.planChanged?.();
    if (this.virtual) this.virtual.stale = true;
    this.redraw();
    this.render();
    clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(() => void this.refreshPreview(), 450);
  }

  private async refreshPreview() {
    if (this.disposed || !this.opened) return;
    const seq = ++this.previewSeq;
    if (!countItems(this.store.doc)) {
      this.preview = null;
      this.render();
      return;
    }
    try {
      const doc = { ...this.store.doc, name: this.store.doc.name || 'draft' };
      const res = await api.planPreview(doc);
      if (seq === this.previewSeq) this.preview = res;
    } catch (e) {
      if (seq === this.previewSeq) {
        this.preview = null;
        this.msg = (e as Error).message;
      }
    }
    this.redraw();
    this.render();
  }

  private levelOf(id: string): 'ok' | 'warning' | 'error' {
    const v = this.preview?.validation;
    if (!v) return 'ok';
    if ([...v.errors, ...v.conflicts].some((m) => m.item_id === id)) return 'error';
    return v.warnings.some((m) => m.item_id === id) ? 'warning' : 'ok';
  }

  private redraw() {
    const items: DrawnItem[] = allItems(this.store.doc).map(({ group, item }) => ({
      id: item.id, group, kind: item.kind, label: this.kind(item.kind)?.label ?? dash(item.kind), x: item.x, y: item.y,
      footprint: footprint(item, this.kind(item.kind)), selected: item.id === this.selectedId, level: this.levelOf(item.id),
    }));
    this.host.draw(items, this.anchor ? [this.anchor.x, this.anchor.y] : null);
  }

  // ---------------------------------------------------------------- rendering

  private render() {
    if (!this.opened) return;
    this.renderLeft();
    this.renderRight();
    this.renderBottom();
  }

  private renderLeft() {
    if (!this.opened) return;
    const d = this.store.doc;
    const canEdit = this.host.role === 'PLANNER' || this.host.role === 'ADMIN';
    const loc = this.anchor?.loc;
    const near = loc?.nearest_lane;
    const where = this.anchor
      ? `<div class="loc"><b>${this.anchor.x.toFixed(1)}, ${this.anchor.y.toFixed(1)}</b>${loc ? (loc.on_road && near
        ? ` · lane <code>${esc(near.id)}</code> ${near.lane_type ?? ''} ${near.speed_limit_kmh ? near.speed_limit_kmh + ' km/h' : ''} (${near.distance_m} m)${loc.junction ? ' · <b>junction</b>' : ''}`
        : ' · not on a road') : ' · looking up…'}
        ${loc?.zones.length ? `<div class="muted">zones nearby: ${loc.zones.map((z) => `${esc(dash(z.type))} ${z.distance_m} m`).join(', ')}</div>` : ''}
        ${loc?.plan_items.length ? `<div class="muted">plan items nearby: ${loc.plan_items.length}</div>` : ''}
        <div class="muted">Right-click the map and choose “Set builder location here” to move it.</div></div>`
      : '<div class="loc muted">Right-click the map to choose a location.</div>';
    const saved = this.store.planId ? `saved v${this.store.version}${this.store.dirty ? ' · <b>unsaved changes</b>' : ''}` : 'not saved yet';
    this.left.innerHTML = `
      <header><strong>Scenario builder</strong><button class="x" data-act="close" title="Close the builder">✕</button></header>
      ${this.msg ? `<div class="bmsg" data-act="clearmsg">${esc(this.msg)}</div>` : ''}
      <div class="plan-bar">
        <input data-f="name" placeholder="Plan name" value="${esc(d.name)}" />
        <div class="row2"><span class="muted">${saved}</span>
          <span class="btns"><button class="ib" data-act="undo" ${this.store.canUndo ? '' : 'disabled'} title="Undo">↶</button><button class="ib" data-act="redo" ${this.store.canRedo ? '' : 'disabled'} title="Redo">↷</button></span></div>
        <div class="row2">
          <select data-f="open"><option value="">Open a saved plan…</option>${this.plans.map((p) => `<option value="${esc(p.id)}">${esc(p.name)} (v${p.latest_version})</option>`).join('')}</select>
          <button class="mini" data-act="new">New</button></div>
      </div>
      ${where}
      <nav class="tabs">${(['prop', 'rule', 'scenario', 'traffic', 'template'] as Tab[]).map((t) => `<button data-tab="${t}" class="${this.tab === t ? 'on' : ''}">${t === 'template' ? 'Templates' : t === 'traffic' ? 'Traffic' : GROUP_TITLE[t]}</button>`).join('')}</nav>
      <div class="tabbody">${this.tab === 'traffic'
        ? (this.tschema ? this.trafficHtml(canEdit) : `<p class="${this.tschemaError ? 'm-err' : 'muted'}">${esc(this.tschemaError || 'Loading the traffic settings…')}</p>`)
        : this.cat ? (this.tab === 'template' ? this.templatesHtml(canEdit) : this.formHtml(this.tab, canEdit)) : '<p class="muted">Loading the catalogue…</p>'}</div>
      <h4>In this plan <span class="muted">(${countItems(d)})</span></h4>
      <ul class="items">${this.itemsHtml()}</ul>
      <div class="foot">
        <label>Why (kept in the history)<input data-f="note" placeholder="optional" /></label>
        <button class="primary" data-act="save" ${canEdit && countItems(d) && d.name.trim() ? '' : 'disabled'} title="${canEdit ? (d.name.trim() ? '' : 'Name the plan first') : 'PLANNER role needed'}">Save plan</button>
      </div>`;
  }

  private formHtml(group: PlanGroup, canEdit: boolean): string {
    const kinds = Object.values(this.cat!.kinds).filter((k) => k.group === group);
    const editing = this.editing ? this.store.find(this.editing)?.item : undefined;
    const cur = (editing && this.kind(editing.kind)?.group === group ? this.kind(editing.kind) : undefined) ?? kinds.find((k) => k.key === this.formKind(group)) ?? kinds[0];
    const p = { ...defaultsFor(cur), ...(editing?.kind === cur.key ? editing.params ?? {} : {}) };
    const fields = cur.params.map((q) => {
      const v = p[q.key];
      const id = `p_${q.key}`;
      const title = q.help ? ` title="${esc(q.help)}"` : '';
      if (q.type === 'enum') return `<label${title}>${esc(q.label)}<select data-p="${q.key}" id="${id}">${(q.options ?? []).map((o) => `<option ${String(v) === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}</select></label>`;
      if (q.type === 'bool') return `<label class="chk"${title}><input type="checkbox" data-p="${q.key}" id="${id}" ${v ? 'checked' : ''}/> ${esc(q.label)}</label>`;
      if (q.type === 'text') return `<label${title}>${esc(q.label)}<input data-p="${q.key}" id="${id}" value="${esc(v ?? '')}"/></label>`;
      return `<label${title}>${esc(q.label)}${q.unit ? ` <span class="muted">(${esc(q.unit)})</span>` : ''}<input type="number" data-p="${q.key}" id="${id}" ${q.min !== undefined ? `min="${q.min}"` : ''} ${q.max !== undefined ? `max="${q.max}"` : ''} step="any" value="${v ?? ''}"/></label>`;
    }).join('');
    const area = cur.target !== 'lane';
    const sch = editing?.schedule;
    return `<label>${editing ? 'Editing' : 'Add'} ${GROUP_TITLE[group].toLowerCase().slice(0, -1)}
        <select data-f="kind" data-group="${group}">${kinds.map((k) => `<option value="${k.key}" ${k.key === cur.key ? 'selected' : ''}>${esc(k.label)}</option>`).join('')}</select></label>
      <div class="support"><span class="sup r-${cur.replay.mode === 'none' ? 'no' : 'yes'}" title="${esc(cur.replay.note || 'judged by the violation-engine replay')}">replay ${cur.replay.mode === 'none' ? '✕' : '✓'}</span>
        <span class="sup v-${cur.virtual?.status ?? 'unsupported'}" title="${esc(cur.virtual?.how ?? '')}">virtual traffic ${CARLA_MARK[cur.virtual?.status ?? 'unsupported']}</span>
        <span class="sup c-${cur.carla.status}" title="${esc(cur.carla.how)}">CARLA ${CARLA_MARK[cur.carla.status]} ${cur.carla.status}</span></div>
      <div class="grid2">${fields}${area ? `<label>Direction <span class="muted">(deg)</span><input type="number" data-f="yaw" step="1" value="${editing?.yaw_deg ?? this.anchor?.loc?.nearest_lane?.heading_deg ?? 0}"/></label>` : ''}</div>
      <details><summary>Priority, schedule, note</summary><div class="grid2">
        <label>Priority<input type="number" data-f="priority" step="1" value="${editing?.priority ?? 0}"/></label>
        <label>Note<input data-f="inote" value="${esc(editing?.note ?? '')}"/></label>
        <label>Active from <span class="muted">(s)</span><input type="number" data-f="from" step="any" value="${sch?.from_s ?? ''}"/></label>
        <label>Active to <span class="muted">(s)</span><input type="number" data-f="to" step="any" value="${sch?.to_s ?? ''}"/></label></div></details>
      <div class="formmsg" id="formmsg"></div>
      <div class="row2"><button class="primary" data-act="${editing ? 'apply' : 'add'}" ${canEdit && this.anchor ? '' : 'disabled'} title="${canEdit ? (this.anchor ? '' : 'Right-click the map first') : 'PLANNER role needed'}">${editing ? 'Apply changes' : `Add at this location`}</button>
        ${editing ? '<button class="mini" data-act="cancel">Cancel</button>' : ''}</div>`;
  }

  private trafficHtml(canEdit: boolean): string {
    const items = allItems(this.store.doc).map((e) => e.item.kind);
    const n = (...k: string[]) => items.filter((x) => k.includes(x)).length;
    return trafficTabHtml(this.tschema!, this.store.doc.traffic, this.host.trafficInfo?.() ?? null, canEdit, !!this.anchor,
      { flows: n('traffic_flow'), groups: n('spawn_vehicles', 'emergency_vehicle'), zones: n('traffic_density', 'peak_hour', 'demand_surge', 'special_event'), incidents: n('accident', 'breakdown', 'congestion_jam') });
  }

  private formKindByGroup: Partial<Record<PlanGroup, string>> = {};
  private formKind(g: PlanGroup) {
    return this.formKindByGroup[g];
  }

  private templatesHtml(canEdit: boolean): string {
    return `<p class="muted">Ready-made starting points; they are added at the builder location and can be edited afterwards.</p>
      ${Object.entries(this.cat!.templates).map(([k, t]) => `<div class="tpl"><b>${esc(t.label)}</b><div class="muted">${t.items.map((i) => esc(dash(i.kind))).join(' + ')}</div>
        <button class="mini" data-tpl="${esc(k)}" ${canEdit && this.anchor ? '' : 'disabled'}>Add at this location</button></div>`).join('')}`;
  }

  private itemsHtml(): string {
    const items = allItems(this.store.doc);
    if (!items.length) return '<li class="muted">Nothing yet. Pick a tab above, set the values and add it.</li>';
    return items.map(({ group, item }) => {
      const k = this.kind(item.kind);
      const lvl = this.levelOf(item.id);
      return `<li data-row="${esc(item.id)}" class="${item.id === this.selectedId ? 'sel' : ''}">
        <i class="dot" style="background:${GROUP_COLOR[group]}"></i>
        <span class="it"><b>${esc(describeItem(item, k))}</b><br><span class="muted">${item.x.toFixed(0)}, ${item.y.toFixed(0)}${item.note ? ' · ' + esc(item.note) : ''}</span></span>
        ${lvl !== 'ok' ? `<span class="lv ${lvl}" title="see the validation list">${lvl === 'error' ? '!' : '?'}</span>` : ''}
        <button class="ib" data-fly="${esc(item.id)}" title="Show on the map">⌖</button><button class="ib" data-edit="${esc(item.id)}" title="Edit">✎</button><button class="ib" data-del="${esc(item.id)}" title="Remove">✕</button></li>`;
    }).join('');
  }

  private current(): { analysis: PlanAnalysis | null; run: Run | null } {
    const run = this.shownRun ? this.runs.find((r) => r.key === this.shownRun) ?? null : null;
    return { analysis: run?.analysis ?? this.preview, run };
  }

  private renderRight() {
    const open = (title: string, body: string, openDefault = true, cls = '') => `<details class="card ${cls}" ${openDefault ? 'open' : ''}><summary>${title}</summary>${body}</details>`;
    if (!countItems(this.store.doc)) {
      this.right.innerHTML = `<header><strong>Recommendations</strong></header><div class="hint"><strong>Nothing to assess yet</strong><span>Add a prop, a rule or a scenario and the impact analysis appears here.</span></div>`;
      return;
    }
    const { analysis: shown, run } = this.current();
    const pre = this.preview;
    const a = shown ?? pre;
    const stale = run?.stale;
    const level = run && !stale ? run.analysis.evidence.level : pre?.evidence.level ?? 'insufficient data';
    const why = run && !stale ? run.analysis.evidence.why : pre?.evidence.why ?? 'checking the plan…';
    const sections = [
      `<div class="status">${evidenceBadge(level)} <span class="muted">${esc(why)}</span>${stale ? '<div class="m-warn">The plan changed since this analysis: analyse again to refresh the measured numbers.</div>' : ''}
        ${this.analysing ? progressHtml('ana', `Analysing “${esc(this.analysing.name)}”`, this.analysing) : ''}</div>`,
      this.simulating ? `<div class="status">${progressHtml('sim', 'Virtual traffic simulation', this.simulating)}</div>` : '',
      this.virtual ? open(`Virtual traffic simulation <span class="tag">simplified model, not CARLA</span>${this.virtual.stale ? ' <span class="tag">outdated</span>' : ''}`, virtualHtml(this.virtual.result)) : '',
      pre ? open('Validation and conflicts', messagesHtml(pre.validation)) : '',
      pre ? open('What the replay can judge', compiledHtml(pre), false) : '',
      a?.observed ? open('Observed baseline (before)', observedHtml(a.observed)) : open('Observed baseline (before)', '<p class="muted">Choose “Analyse on recorded traffic” in the bottom bar to measure speed, queue, density and travel time around the plan on the session’s recorded traffic.</p>', false),
      open('Violations before → after <span class="tag">replay (measured)</span>', a?.replay ? replayHtml(a) : replayHtml({ ...(pre as PlanAnalysis), replay: undefined })),
      open('Best case / worst case', a ? bestWorstHtml(a) : ''),
      pre ? open('Risks <span class="tag">rule of thumb, not measured</span>', risksHtml(pre)) : '',
      pre ? open('Alternatives', alternativesHtml(pre.alternatives)) : '',
      pre ? open('After the change: not simulated yet', pendingHtml(pre), false) : '',
      open('Scenario comparison', compareHtml(this.compareRows())),
    ];
    this.right.innerHTML = `<header><strong>Recommendations</strong><span class="muted">${esc(this.store.doc.name || 'draft plan')}</span></header>${sections.join('')}`;
  }

  private compareRows(): CompareRow[] {
    return this.runs.filter((r) => r.analysis.replay).map((r) => ({
      name: r.name, evidence: r.stale ? 'preliminary' : r.analysis.evidence.level, before: r.analysis.replay!.baseline.counted, after: r.analysis.replay!.modified.counted,
      added: r.analysis.replay!.added.length, removed: r.analysis.replay!.removed.length, current: r.key === this.shownRun,
    }));
  }

  private renderBottom() {
    const canEdit = this.host.role === 'PLANNER' || this.host.role === 'ADMIN';
    const sid = this.host.sessionId();
    const st = this.host.player.read();
    this.bottom.innerHTML = `
      <div class="grp"><button class="ib" data-act="preset" title="Back to the start">⏮</button>
        <button class="ib big" data-act="play" title="Play / pause the recorded traffic">${st.playing ? '⏸' : '▶'}</button>
        <button class="ib" data-act="stopp" title="Stop and rewind">⏹</button>
        <select data-f="speed" title="Simulation speed"><option>0.5</option><option>1</option><option selected>2</option><option>4</option><option>8</option></select><span class="muted">×</span></div>
      <div class="grp wide"><input type="range" data-f="tl" min="0" max="1000" value="${st.tMax > st.tMin ? Math.round(((st.t - st.tMin) / (st.tMax - st.tMin)) * 1000) : 0}" title="Timeline" /><span id="tlabel" class="muted"></span></div>
      <div class="grp"><label class="inl" title="Virtual traffic on the map (blue); with items in the plan it follows the plan. Simplified model, not CARLA."><input type="checkbox" data-f="custom" ${this.host.customTraffic.isOn() ? 'checked' : ''} /> Custom traffic</label></div>
      <div class="grp"><label class="inl">Scenario <select data-f="view"><option value="">Draft (live preview)</option>${this.runs.map((r) => `<option value="${r.key}" ${r.key === this.shownRun ? 'selected' : ''}>${esc(r.name)}${r.stale ? ' (outdated)' : ''}</option>`).join('')}</select></label></div>
      <div class="grp"><button class="primary" data-act="analyze" ${canEdit && countItems(this.store.doc) && sid && !this.analysing ? '' : 'disabled'} title="${sid ? 'Replay the session’s recorded traffic with this plan' : 'Pick a recorded session in the info panel first'}">Analyse on recorded traffic</button>
        <button class="primary" data-act="simulate" ${canEdit && countItems(this.store.doc) && !this.simulating ? '' : 'disabled'} title="Run vehicles on the map with this plan and with the baseline (virtual traffic model, several seeds), then play them here">Simulate virtual traffic</button>
        <button data-act="carla" disabled title="Phase 2: runs in CarlaAir on the simulator PC (baseline vs modified, several seeds). Not built yet.">Run in CARLA</button>
        <button data-act="report" ${countItems(this.store.doc) ? '' : 'disabled'} title="Planning report: PDF, Excel or CSV, with the observed, replayed and simulated results kept apart">Report</button></div>
      ${this.reportOpen ? this.reportHtml(canEdit) : ''}`;
    this.updateClock();
  }

  private reportHtml(canEdit: boolean): string {
    const saved = this.store.planId;
    return `<div class="tw-rep"><header><b>Planning report</b><button class="x" data-act="repclose">✕</button></header>
      <div class="grid2"><label>Format<select data-f="repfmt"><option value="pdf">PDF report</option><option value="xlsx">Excel (one sheet per section)</option><option value="csv">CSV (all numbers, with their source)</option></select></label>
        <label class="chk"><input type="checkbox" data-f="repshot" checked /> Include a screenshot of the 3D view</label></div>
      <p class="muted">Contains: scenario overview, applied modifications, ${this.virtual ? 'virtual-simulation results, ' : ''}${this.runs.length ? 'recorded-traffic analysis, ' : ''}risks and alternatives, the comparison, and the limits. Every number says where it comes from.</p>
      <div class="row2"><button class="primary" data-act="repgo" ${canEdit && !this.reportBusy ? '' : 'disabled'}>${this.reportBusy ? 'Making the report…' : 'Generate and download'}</button><span class="muted">${esc(this.reportMsg)}</span></div>
      ${saved ? `<h5>Earlier reports of this plan</h5><ul class="rlist">${this.reports.map((r) => `<li><span class="muted">${new Date(r.at).toLocaleString()} · v${r.plan_version ?? '?'} · ${r.format.toUpperCase()} · ${esc(r.by)}</span>
        <button class="mini" data-repdl="${esc(r.id)}">Download</button> <button class="mini" data-repopen="${esc(r.id)}" title="Reopen the scenario as it was in this report">Open scenario</button></li>`).join('') || '<li class="muted">none yet</li>'}</ul>` : '<p class="muted">Save the plan to keep its reports and reopen the scenario from them.</p>'}</div>`;
  }

  private async makeReport() {
    const fmt = (this.bottom.querySelector<HTMLSelectElement>('select[data-f=repfmt]')?.value ?? 'pdf') as 'pdf' | 'xlsx' | 'csv';
    const shot = this.bottom.querySelector<HTMLInputElement>('input[data-f=repshot]')?.checked ? this.host.screenshot() : null;
    this.reportBusy = true;
    this.reportMsg = '';
    this.renderBottom();
    try {
      const { analysis: shown, run } = this.current();
      const a = run && !run.stale ? run.analysis : this.preview ?? shown;
      const v = this.virtual?.result;
      const body = {
        plan: { ...this.store.doc, name: this.store.doc.name || 'draft plan' }, plan_id: this.store.planId, version: this.store.planId ? this.store.version : null,
        analysis: a, comparison: this.compareRows(), screenshots: shot ? [shot] : [], format: fmt, session_id: this.host.sessionId() || null,
        analysed_at: run ? new Date(run.at).toISOString() : null,
        virtual: v ? { ...v, trajectories: undefined } : null,
      };
      const rec = await api.makeReport(body);
      await this.downloadReport(rec);
      this.reportMsg = `Saved as ${rec.filename}`;
      if (this.store.planId) this.reports = await api.planReports(this.store.planId);
    } catch (e) {
      this.reportMsg = `Failed: ${(e as Error).message}`;
    } finally {
      this.reportBusy = false;
      this.renderBottom();
    }
  }

  private async downloadReport(rec: { download_url: string; filename: string }) {
    const blob = await api.blob(rec.download_url);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = rec.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  private updateClock() {
    if (!this.opened) return;
    const st = this.host.player.read();
    const l = this.bottom.querySelector<HTMLElement>('#tlabel');
    if (l) l.textContent = st.tMax > st.tMin ? `${(st.t - st.tMin).toFixed(0)} / ${(st.tMax - st.tMin).toFixed(0)} s` : 'no recorded traffic loaded';
    const tl = this.bottom.querySelector<HTMLInputElement>('input[data-f=tl]');
    if (tl && document.activeElement !== tl && st.tMax > st.tMin) tl.value = String(Math.round(((st.t - st.tMin) / (st.tMax - st.tMin)) * 1000));
    const play = this.bottom.querySelector<HTMLElement>('[data-act=play]');
    if (play) play.textContent = st.playing ? '⏸' : '▶';
    this.updateBars();
  }

  /** Move the progress bars without redrawing the panel (called by the poll loops and the clock). */
  private updateBars() {
    for (const [id, st] of [['ana', this.analysing], ['sim', this.simulating]] as const) {
      const box = this.right.querySelector<HTMLElement>(`[data-pbar="${id}"]`);
      if (!box || !st) continue;
      const frac = st.total > 0 ? Math.min(st.done / st.total, 1) : 0;
      box.querySelector<HTMLElement>('i')!.style.width = `${Math.max(frac * 100, 4)}%`;
      box.querySelector<HTMLElement>('.pl')!.textContent = `${st.stage} · step ${Math.min(st.done + 1, st.total)} of ${st.total}`;
      box.querySelector<HTMLElement>('.pe')!.textContent = `${Math.round((Date.now() - st.since) / 1000)} s elapsed`;
    }
  }

  // ---------------------------------------------------------------- events

  private readForm(): { item: Partial<PlanItem>; kind: PlanKind; problems: string[] } | null {
    const sel = this.left.querySelector<HTMLSelectElement>('select[data-f=kind]');
    const kind = sel ? this.kind(sel.value) : undefined;
    if (!kind) return null;
    const params: Record<string, unknown> = {};
    for (const q of kind.params) {
      const el = this.left.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-p="${q.key}"]`);
      if (!el) continue;
      if (q.type === 'bool') params[q.key] = (el as HTMLInputElement).checked;
      else if (q.type === 'number') params[q.key] = el.value === '' ? null : Number(el.value);
      else params[q.key] = el.value;
    }
    const num = (f: string) => {
      const v = this.left.querySelector<HTMLInputElement>(`[data-f=${f}]`)?.value;
      return v === undefined || v === '' ? undefined : Number(v);
    };
    const item: Partial<PlanItem> = { kind: kind.key, params };
    const yaw = num('yaw');
    if (yaw !== undefined) item.yaw_deg = yaw;
    item.priority = num('priority') ?? 0;
    const note = this.left.querySelector<HTMLInputElement>('[data-f=inote]')?.value.trim();
    if (note) item.note = note;
    const from = num('from'), to = num('to');
    if (from !== undefined || to !== undefined) item.schedule = { from_s: from ?? null, to_s: to ?? null };
    return { item, kind, problems: paramProblems(kind, params) };
  }

  private async onClick(e: Event) {
    const t = e.target as HTMLElement;
    const btn = t.closest<HTMLElement>('[data-tscn],[data-tpreset],[data-tadd],[data-repdl],[data-repopen],[data-act],[data-tab],[data-tpl],[data-fly],[data-edit],[data-del],[data-alt],[data-play],.fly,[data-item]');
    if (!btn) return;
    const act = btn.dataset.act;
    if (btn.dataset.tab) {
      this.tab = btn.dataset.tab as Tab;
      this.editing = null;
      return this.renderLeft();
    }
    if (btn.dataset.tpl && this.cat && this.anchor) {
      const made = this.store.addTemplate(this.cat, btn.dataset.tpl, this.anchor.x, this.anchor.y, this.anchor.loc?.nearest_lane?.id);
      this.selectedId = made[0]?.id ?? null;
      return;
    }
    if (btn.dataset.fly) {
      const it = this.store.find(btn.dataset.fly)?.item;
      if (it) {
        this.selectedId = it.id;
        this.host.flyTo(it.x, it.y);
        this.redraw();
        this.renderLeft();
      }
      return;
    }
    if (btn.dataset.edit) {
      const f = this.store.find(btn.dataset.edit);
      if (f) {
        this.editing = f.item.id;
        this.selectedId = f.item.id;
        this.tab = f.group;
        this.formKindByGroup[f.group] = f.item.kind;
        this.renderLeft();
        this.redraw();
      }
      return;
    }
    if (btn.dataset.del) {
      this.store.remove(btn.dataset.del);
      if (this.editing === btn.dataset.del) this.editing = null;
      return;
    }
    if (btn.dataset.tscn && this.tschema && this.cat && this.anchor) {
      const sp = this.tschema.scenario_presets[btn.dataset.tscn];
      if (sp) {
        const lane = this.anchor.loc?.nearest_lane?.id;
        const made = this.store.addTrafficScenario(this.cat, sp, this.anchor.x, this.anchor.y, lane);
        this.selectedId = made[0]?.id ?? null;
        this.msg = `Added the “${sp.label}” scenario: ${made.length} item${made.length === 1 ? '' : 's'} at ${this.anchor.x.toFixed(0)}, ${this.anchor.y.toFixed(0)}; the traffic settings follow it.`;
        this.renderLeft();
      }
      return;
    }
    if (btn.dataset.tpreset && this.tschema) {
      this.store.setTraffic({ preset: btn.dataset.tpreset });
      return;
    }
    if (btn.dataset.tadd) {
      this.tab = 'scenario';
      this.formKindByGroup.scenario = btn.dataset.tadd;
      this.editing = null;
      return this.renderLeft();
    }
    if (btn.dataset.repdl) {
      const r = this.reports.find((q) => q.id === btn.dataset.repdl);
      if (r) await this.downloadReport(r).catch((e) => { this.reportMsg = `Failed: ${(e as Error).message}`; this.renderBottom(); });
      return;
    }
    if (btn.dataset.repopen) {
      const r = this.reports.find((q) => q.id === btn.dataset.repopen);
      if (r?.plan_id) {
        const row = await api.plan(r.plan_id, r.plan_version ?? undefined);
        this.store.load(row.plan, row.id, row.version);
        this.runs = [];
        this.virtual = null;
        this.shownRun = null;
        this.msg = `Opened ${row.name} v${row.version} from the report`;
        void this.refreshPreview();
        this.render();
      }
      return;
    }
    if (btn.dataset.alt !== undefined) return void this.testAlternative(Number(btn.dataset.alt));
    if (btn.dataset.play) {
      const w = btn.dataset.play;
      if (w === 'recorded' || !this.virtual) {
        this.playing = null;
        this.host.showVirtual(null);
      } else {
        this.playing = w as 'baseline' | 'plan';
        this.host.showVirtual(this.virtual.result.trajectories[w as 'baseline' | 'plan'], w === 'plan' ? this.virtual.result.signals ?? null : null);
      }
      return;
    }
    if (btn.classList.contains('fly') && btn.dataset.x) return this.host.flyTo(Number(btn.dataset.x), Number(btn.dataset.y));
    if (btn.dataset.item) {
      this.selectedId = btn.dataset.item;
      this.redraw();
      this.renderLeft();
      return;
    }
    switch (act) {
      case 'close': return this.close();
      case 'clearmsg': this.msg = ''; return this.renderLeft();
      case 'undo': return this.store.undo();
      case 'redo': return this.store.redo();
      case 'new': this.store.load(emptyPlan(this.host.town(), this.host.sessionId() || null)); this.runs = []; this.virtual = null; if (this.playing) this.host.showVirtual(null); this.playing = null; this.shownRun = null; this.preview = null; this.editing = null; this.selectedId = null; return this.render();
      case 'cancel': this.editing = null; return this.renderLeft();
      case 'add': case 'apply': return this.addOrApply(act === 'apply');
      case 'save': return void this.save();
      case 'analyze': return void this.analyse();
      case 'simulate': return void this.simulate();
      case 'treset': return this.store.setTraffic(null);
      case 'texport': return this.exportTraffic();
      case 'report':
        this.reportOpen = !this.reportOpen;
        if (this.reportOpen && this.store.planId) void api.planReports(this.store.planId).then((r) => { this.reports = r; this.renderBottom(); }).catch(() => undefined);
        return this.renderBottom();
      case 'repclose': this.reportOpen = false; return this.renderBottom();
      case 'repgo': return void this.makeReport();
      case 'play': this.host.player.toggle(); return this.updateClock();
      case 'preset': return this.host.player.reset();
      case 'stopp': this.host.player.stop(); return this.updateClock();
    }
  }

  private addOrApply(apply: boolean) {
    const form = this.readForm();
    const out = this.left.querySelector<HTMLElement>('#formmsg');
    if (!form || !this.cat) return;
    if (form.problems.length) {
      if (out) out.innerHTML = form.problems.map((p) => `<div class="m-err">${esc(p)}</div>`).join('');
      return;
    }
    if (apply && this.editing) {
      const keep = this.store.find(this.editing)?.item;
      const patch: Partial<PlanItem> = { ...form.item, schedule: form.item.schedule };
      if (!form.item.schedule && keep?.schedule) patch.schedule = undefined;
      this.store.update(this.editing, patch);
      this.editing = null;
      return;
    }
    if (!this.anchor) return;
    const lane = form.kind.target === 'lane' || form.kind.target === 'area' ? this.anchor.loc?.nearest_lane?.id : undefined;
    const item = this.store.add(form.kind.group, form.kind, this.anchor.x, this.anchor.y, {
      ...form.item, params: form.item.params, ...(lane ? { target: { lane_id: lane } } : {}),
    });
    this.selectedId = item.id;
  }

  private async onChangeEvent(e: Event) {
    const t = e.target as HTMLInputElement | HTMLSelectElement;
    const f = t.dataset.f;
    if (f === 'kind') {
      const g = (t.dataset.group ?? this.tab) as PlanGroup;
      this.formKindByGroup[g] = t.value;
      this.editing = null;
      return this.renderLeft();
    }
    if (f === 'name') return this.store.setMeta({ name: t.value });
    if (f === 'open' && t.value) {
      const row = await api.plan(t.value);
      this.store.load(row.plan, row.id, row.version);
      this.runs = [];
      this.shownRun = null;
      this.selectedId = null;
      this.editing = null;
      this.msg = `Opened ${row.name} v${row.version}`;
      void this.refreshPreview();
      return this.render();
    }
    if (f === 'view') {
      this.shownRun = t.value || null;
      return this.render();
    }
    if (f === 'speed') return this.host.player.setSpeed(Number(t.value));
    if (t.dataset.tf && this.tschema) return this.editTraffic(t);
    if (f === 'timport') return void this.importTraffic(t as HTMLInputElement);
    if (f === 'custom') return this.host.customTraffic.set((t as HTMLInputElement).checked);
  }

  private onInput(e: Event) {
    const t = e.target as HTMLInputElement;
    if (t.dataset.tfr) { // the slider next to a number: keep the number box in step while dragging; the change event applies it
      const box = this.left.querySelector<HTMLInputElement>(`input[data-tf="${t.dataset.tfr}"]`);
      if (box) box.value = t.value;
      return;
    }
    if (t.dataset.f === 'tl') this.host.player.seek(Number(t.value) / 1000);
  }

  // ---------------------------------------------------------------- custom traffic settings

  private editTraffic(el: HTMLInputElement | HTMLSelectElement) {
    const key = el.dataset.tf!;
    const sch = this.tschema!;
    const field = sch.sections.flatMap((s) => s.fields).find((f) => f.key === key);
    if (!field) return;
    const v: unknown = field.type === 'bool' ? (el as HTMLInputElement).checked : field.type === 'number' ? (el.value === '' ? null : Number(el.value)) : el.value;
    const next = setPath(normalizeTraffic(sch, this.store.doc.traffic), key, v) as Cfg;
    next.preset = 'custom';
    const problems = trafficProblems(sch, next);
    const msg = this.left.querySelector<HTMLElement>('#tmsg');
    if (problems.length) {
      if (msg) msg.innerHTML = problems.map((p) => `<div class="m-err">${esc(p)}</div>`).join('');
      return;
    }
    this.store.setTraffic(next);
  }

  private exportTraffic() {
    if (!this.tschema) return;
    const cfg = normalizeTraffic(this.tschema, this.store.doc.traffic);
    const blob = new Blob([JSON.stringify(cfg, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `custom_traffic_${(this.store.doc.name || 'settings').replace(/[^A-Za-z0-9_-]+/g, '_')}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }

  private async importTraffic(input: HTMLInputElement) {
    const msg = this.left.querySelector<HTMLElement>('#tmsg');
    const file = input.files?.[0];
    input.value = '';
    if (!file || !this.tschema) return;
    try {
      const cfg = JSON.parse(await file.text()) as Cfg;
      if (cfg === null || typeof cfg !== 'object' || Array.isArray(cfg)) throw new Error('not a settings object');
      const next = normalizeTraffic(this.tschema, cfg);
      next.preset = cfg.preset && this.tschema.presets[String(cfg.preset)] ? cfg.preset : 'custom';
      const problems = trafficProblems(this.tschema, next);
      if (problems.length) throw new Error(problems.slice(0, 3).join('; '));
      this.store.setTraffic(next);
      this.msg = `Imported traffic settings from ${file.name}`;
      this.renderLeft();
    } catch (e) {
      if (msg) msg.innerHTML = `<div class="m-err">Not imported: ${esc((e as Error).message)}</div>`;
    }
  }

  // ---------------------------------------------------------------- save / analyse

  private async save() {
    const d = this.store.doc;
    const note = this.left.querySelector<HTMLInputElement>('[data-f=note]')?.value.trim() || undefined;
    try {
      const row = this.store.planId
        ? await api.savePlan(this.store.planId, { ...d }, this.store.version, note)
        : await api.createPlan({ ...d }, note);
      this.store.markSaved(row.id, row.latest_version, row.plan);
      this.msg = `Saved ${row.name} as version ${row.latest_version}.`;
      void this.loadPlans();
    } catch (e) {
      this.msg = `Not saved: ${(e as Error).message}`;
    }
    this.renderLeft();
  }

  private async analyse(doc: PlanDoc = this.store.doc, name = this.store.doc.name || 'draft plan') {
    const sid = this.host.sessionId();
    if (!sid) {
      this.msg = 'Pick a recorded session in the info panel first: the replay needs its traffic.';
      return this.renderLeft();
    }
    this.analysing = { since: Date.now(), name, stage: 'Starting', done: 0, total: 4 };
    this.render();
    try {
      let run: PlanRun = await api.analyzePlan({ plan: { ...doc, name }, session_id: sid });
      while (run.status === 'running') {
        await new Promise((r) => setTimeout(r, 1000));
        if (this.disposed) return;
        run = await api.planRun(run.id);
        if (run.progress && this.analysing) Object.assign(this.analysing, run.progress);
        this.updateBars();
      }
      if (run.status === 'failed' || !run.result) throw new Error(run.error ?? 'the analysis failed');
      const key = run.id;
      this.runs = [...this.runs.filter((r) => r.name !== name), { key, name, analysis: run.result, at: Date.now(), stale: false }];
      this.shownRun = key;
      this.msg = '';
    } catch (e) {
      this.msg = `Analysis failed: ${(e as Error).message}`;
    } finally {
      this.analysing = null;
      this.render();
    }
  }

  /** Virtual traffic (ml/planning/microsim.py): baseline vs the plan on the same seeds, then play the cars in the twin. */
  private async simulate() {
    if (!countItems(this.store.doc)) return;
    this.simulating = { since: Date.now(), stage: 'Starting', done: 0, total: 10 };
    this.render();
    try {
      const doc = { ...this.store.doc, name: this.store.doc.name || 'draft plan' };
      let run: PlanRun = await api.simulatePlan({ plan: doc, seeds: 5, duration_s: 120 });
      while (run.status === 'running') {
        await new Promise((r) => setTimeout(r, 700));
        if (this.disposed) return;
        run = await api.planRun(run.id);
        if (run.progress && this.simulating) Object.assign(this.simulating, run.progress);
        this.updateBars();
      }
      if (run.status === 'failed' || !run.result) throw new Error(run.error ?? 'the simulation failed');
      this.virtual = { result: run.result as unknown as VirtualResult, name: doc.name, stale: false };
      this.msg = '';
      this.playing = 'plan';
      this.host.showVirtual(this.virtual.result.trajectories.plan, this.virtual.result.signals ?? null);
    } catch (e) {
      this.msg = `Simulation failed: ${(e as Error).message}`;
    } finally {
      this.simulating = null;
      this.render();
    }
  }

  private async testAlternative(i: number) {
    const alt = this.preview?.alternatives[i];
    if (!alt || !this.cat) return;
    const variant = applyAlternative(this.store.doc, alt, this.cat);
    if (!variant) {
      this.msg = 'That alternative has no patch the replay can test.';
      return this.renderLeft();
    }
    // the base plan first (so the comparison has its own row), then the variant
    if (!this.runs.some((r) => r.name === (this.store.doc.name || 'draft plan') && !r.stale)) await this.analyse();
    await this.analyse(variant, variant.name);
  }
}
