/**
 * HTML for the recommendation side panel (pure functions, unit tested). Every block says where its numbers come from:
 * "observed" (recorded traffic before the change), "replay (measured)" (violation engine on that traffic with the change),
 * "rule of thumb (not measured)", and the metrics that need a CARLA run are listed as not simulated yet.
 */
import type { PlanAlternative, PlanAnalysis, PlanMessage, PlanReplayEvent, VirtualResult } from '../../api/types';

export const esc = (s: unknown) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

const EVIDENCE_HELP: Record<string, string> = {
  preliminary: 'Rule of thumb only: no measurement behind it yet.',
  'replay-validated': 'The violation engine was re-run on recorded traffic with the change (measured). Driver response is not modelled.',
  'simulation-validated': 'Backed by CARLA baseline-vs-modified runs.',
  'insufficient data': 'Not enough to say anything yet.',
};

export function evidenceBadge(level: string): string {
  return `<span class="rb rb-${esc(level.replace(/\s+/g, '-'))}" title="${esc(EVIDENCE_HELP[level] ?? '')}">${esc(level)}</span>`;
}

export function messagesHtml(v: PlanAnalysis['validation']): string {
  const rows = (list: PlanMessage[], cls: string, label: string) =>
    list.map((m) => `<li class="${cls}" data-item="${esc(m.item_id ?? '')}"><b>${label}</b> ${esc(m.message)}</li>`).join('');
  const html = rows(v.errors, 'm-err', 'Error') + rows(v.conflicts, 'm-conf', 'Conflict') + rows(v.warnings, 'm-warn', 'Warning') + rows(v.infos, 'm-info', 'Note');
  return html ? `<ul class="rlist">${html}</ul>` : '<p class="muted">No problems found.</p>';
}

const num = (v: number | null | undefined, unit = '', d = 1) => (v === null || v === undefined ? '-' : `${Number(v.toFixed(d))}${unit ? ' ' + unit : ''}`);

export function observedHtml(o: NonNullable<PlanAnalysis['observed']>): string {
  const rows: [string, string][] = [
    ['Vehicles seen', String(o.vehicles)],
    ['Mean / 85th-percentile speed', `${num(o.mean_speed_kmh, 'km/h')} / ${num(o.p85_speed_kmh, 'km/h')}`],
    ['Density', num(o.density_veh_per_km2, 'veh/km²', 0)],
    ['Flow', num(o.flow_veh_per_h, 'veh/h', 0)],
    ['Queue length (max / mean)', `${num(o.queue.max_m, 'm')} / ${num(o.queue.mean_m, 'm')}`],
    ['Travel time through the area (mean / p85)', `${num(o.travel_time.mean_s, 's')} / ${num(o.travel_time.p85_s, 's')} (${o.travel_time.vehicles} vehicles)`],
    ['Conflicts (time-to-collision < 1.5 s)', String(o.conflicts_ttc.count)],
  ];
  return `<table class="rtab">${rows.map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join('')}</table>
    <p class="muted">${esc(o.label)}: ${Math.round(o.radius_m)} m around the plan, ${Math.round(o.duration_s)} s of recording.</p>`;
}

/** Grouped bars: violations before / after per type, plus the totals. Inline SVG, no library. */
export function beforeAfterChart(base: Record<string, number>, mod: Record<string, number>): string {
  const types = [...new Set([...Object.keys(base), ...Object.keys(mod)])].sort();
  if (!types.length) return '<p class="muted">No violations in either run.</p>';
  const max = Math.max(1, ...types.map((t) => Math.max(base[t] ?? 0, mod[t] ?? 0)));
  const rowH = 26, w = 320, left = 112;
  const bars = types.map((t, i) => {
    const y = i * rowH + 4, b = base[t] ?? 0, m = mod[t] ?? 0;
    const bw = ((w - left - 30) * b) / max, mw = ((w - left - 30) * m) / max;
    return `<text x="0" y="${y + 14}" class="cl">${esc(t.replace(/_/g, ' '))}</text>
      <rect x="${left}" y="${y}" width="${Math.max(bw, b ? 2 : 0)}" height="9" rx="2" class="cb"><title>before: ${b}</title></rect>
      <text x="${left + bw + 4}" y="${y + 8}" class="cn">${b}</text>
      <rect x="${left}" y="${y + 11}" width="${Math.max(mw, m ? 2 : 0)}" height="9" rx="2" class="${m > b ? 'cw' : m < b ? 'cg' : 'ca'}"><title>after: ${m}</title></rect>
      <text x="${left + mw + 4}" y="${y + 19}" class="cn">${m}</text>`;
  }).join('');
  return `<svg viewBox="0 0 ${w} ${types.length * rowH + 8}" class="chart" role="img" aria-label="violations before and after per type">${bars}</svg>
    <p class="legend2"><i class="cb"></i>before <i class="ca"></i>after (same / <i class="cg"></i>fewer / <i class="cw"></i>more)</p>`;
}

function eventLinks(list: PlanReplayEvent[], cap = 8): string {
  return list.slice(0, cap).map((e) => {
    const where = e.x !== null && e.y !== null ? ` data-x="${e.x}" data-y="${e.y}"` : '';
    return `<li class="fly"${where}>${esc((e.type || '').replace(/_/g, ' '))} ${e.condition ? `(${esc(e.condition)})` : ''} at ${e.flag_s !== null ? Math.round(e.flag_s) + ' s' : '?'}</li>`;
  }).join('') + (list.length > cap ? `<li class="muted">+ ${list.length - cap} more</li>` : '');
}

export function replayHtml(a: PlanAnalysis): string {
  const r = a.replay;
  if (!r) return '<p class="muted">Nothing in this plan changes a rule the replay can judge, so there is no before / after yet.</p>';
  const d = r.modified.counted - r.baseline.counted;
  return `<div class="bigs"><div><span>before</span><b>${r.baseline.counted}</b></div><div class="arrow">→</div>
      <div class="${d > 0 ? 'worse' : d < 0 ? 'better' : ''}"><span>after</span><b>${r.modified.counted}</b></div>
      <div><span>change</span><b>${d > 0 ? '+' : ''}${d}</b></div></div>
    ${beforeAfterChart(r.baseline.by_type, r.modified.by_type)}
    <p class="muted"><b>${esc(r.label)}.</b> ${esc(r.note)} Scope: ${esc(r.scope)}; ${r.rows} rows, ${r.tracks} vehicles, ${r.seconds} s.</p>`;
}

export function bestWorstHtml(a: PlanAnalysis): string {
  if (!a.best_case || !a.worst_case) return '<p class="muted">Available once the replay has run on a session.</p>';
  const col = (title: string, cls: string, count: number, list: PlanReplayEvent[], note: string) =>
    `<div class="bw ${cls}"><h5>${title}</h5><b>${count}</b><ul class="rlist">${eventLinks(list)}</ul><p class="muted">${esc(note)}</p></div>`;
  return `<div class="bwgrid">${col('Best case: violations removed', 'bwg', a.best_case.count, a.best_case.removed, a.best_case.note)}
    ${col('Worst case: new violations', 'bwr', a.worst_case.count, a.worst_case.added, a.worst_case.note)}</div>
    <p class="muted">Both are on the recorded traffic: the same vehicles making the same movements.</p>`;
}

export function risksHtml(a: PlanAnalysis): string {
  if (!a.risks.length) return '<p class="muted">No rule-of-thumb risks found for this plan.</p>';
  return `<ul class="rlist">${a.risks.map((r) => `<li class="${r.level === 'warning' ? 'm-warn' : 'm-info'}" data-item="${esc(r.item_id)}">${esc(r.message)} <span class="tag">${esc(r.basis)}</span></li>`).join('')}</ul>`;
}

export function alternativesHtml(alts: PlanAlternative[]): string {
  if (!alts.length) return '<p class="muted">No alternatives to suggest for these items yet.</p>';
  return alts.map((a, i) => `<div class="alt"><div class="alt-h"><b>${esc(a.title)}</b> ${evidenceBadge(a.evidence)}</div>
    <p><span class="k">Benefit</span> ${esc(a.benefit)}</p><p><span class="k">Risk</span> ${esc(a.risk)}</p><p><span class="k">Trade-off</span> ${esc(a.tradeoff)}</p>
    <p class="muted">${esc(a.evidence_note)}</p>
    ${a.test ? `<button class="mini" data-alt="${i}">Test this alternative</button>` : '<button class="mini" disabled title="needs a CARLA run (phase 2)">Needs a CARLA run</button>'}</div>`).join('');
}

export function compiledHtml(a: PlanAnalysis): string {
  const ev = a.compiled.evaluated.map((e) => `<li><b>${esc(e.kind.replace(/_/g, ' '))}</b>: ${esc(e.what)}${e.note ? ` <span class="muted">${esc(e.note)}</span>` : ''}</li>`).join('');
  const sk = a.compiled.not_evaluated.map((e) => `<li><b>${esc(e.kind.replace(/_/g, ' '))}</b> <span class="muted">${esc(e.reason)}</span></li>`).join('');
  return `${ev ? `<h5>Judged by the replay</h5><ul class="rlist">${ev}</ul>` : ''}${sk ? `<h5>Not evaluated (kept in the plan, no numbers)</h5><ul class="rlist">${sk}</ul>` : ''}` || '<p class="muted">Add something to the plan.</p>';
}

export function pendingHtml(a: PlanAnalysis): string {
  return `<p class="muted">Not simulated yet (needs a CARLA baseline-vs-modified run):</p><ul class="rlist pend">${a.pending_metrics.map((m) => `<li>${esc(m)}</li>`).join('')}</ul>`;
}

export interface CompareRow { name: string; evidence: string; before: number | null; after: number | null; added: number | null; removed: number | null; current: boolean }

export function compareHtml(rows: CompareRow[]): string {
  if (rows.length < 2) return '<p class="muted">Test an alternative or analyse another plan on the same session to compare here.</p>';
  const best = Math.min(...rows.filter((r) => r.after !== null).map((r) => r.after as number));
  return `<table class="rtab cmp"><tr><th>Scenario</th><th>Violations before → after</th><th>+ new / − removed</th></tr>${rows.map((r) => `<tr class="${r.current ? 'cur' : ''}">
    <td>${esc(r.name)} ${evidenceBadge(r.evidence)}</td><td>${r.before ?? '-'} → <b class="${r.after === best ? 'better' : ''}">${r.after ?? '-'}</b></td><td>${r.added ?? '-'} / ${r.removed ?? '-'}</td></tr>`).join('')}</table>
    <p class="muted">Same recorded traffic for every row. Fewer violations is one objective; travel time, throughput and safety need CARLA runs, so the trade-offs are not ranked here.</p>`;
}

const vnum = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? '-' : String(Number(v.toFixed(d))));

/** Virtual simulation (simplified model, not CARLA): per metric the baseline and the plan, mean ± 95 % interval over the seeds. */
export function virtualHtml(r: VirtualResult): string {
  const rows = Object.values(r.metrics).filter((m) => m.baseline.mean !== null || m.plan.mean !== null).map((m) => {
    const cell = (x: { mean: number | null; ci95: number | null }) => `${vnum(x.mean)}${x.ci95 !== null ? ` <span class="muted">± ${vnum(x.ci95)}</span>` : ''}`;
    const d = m.diff;
    const cls = m.direction === 'better' ? 'better' : m.direction === 'worse' ? 'worse' : '';
    return `<tr><td>${esc(m.label)}${m.unit ? ` <span class="muted">(${esc(m.unit)})</span>` : ''}</td><td>${cell(m.baseline)}</td><td>${cell(m.plan)}</td>
      <td><b class="${cls}">${d === null ? '-' : (d > 0 ? '+' : '') + vnum(d)}</b><br><span class="muted">${esc(m.verdict)}</span></td></tr>`;
  }).join('');
  return `<table class="rtab"><tr><th>Metric</th><th>Baseline</th><th>With the plan</th><th>Change</th></tr>${rows}</table>
    <div class="row2 playrow"><button class="mini" data-play="baseline">▶ Play baseline traffic</button><button class="mini primary" data-play="plan">▶ Play with the plan</button><button class="mini" data-play="recorded">Back to recording</button></div>
    <p class="muted"><b>${esc(r.label)}.</b> ${r.seeds} runs per variant of ${r.duration_s} s (after ${r.warmup_s} s warm-up), mean ± 95 % interval over the seeds; “different” only when the intervals are apart.
    Measured inside ${r.region.radius_m ? Math.round(r.region.radius_m) + ' m around the plan' : 'the network'}.</p>
    <details><summary class="muted">What this model does not do</summary><ul class="rlist">${r.notes.map((n) => `<li class="m-info">${esc(n)}</li>`).join('')}</ul></details>`;
}
