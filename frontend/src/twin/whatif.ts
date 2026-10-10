/**
 * What-if scenarios in the twin (PRD 21.3, Expected_Output 7.4): request building and result text,
 * pure and unit tested. The backend (POST /api/scenarios, ml/planning/whatif.py) answers twice:
 * a rule replay on the recorded traffic (measured) and a countermeasure projection (sourced factor,
 * always "projected estimate"); the two are shown apart and never added up.
 */
import type { LaneOverride, Zone } from "./types";

export interface Countermeasure { key: string; name: string; targets: string[]; quantified: boolean; source: string; url: string }

export interface ScenarioRequest {
  session_id: string;
  name: string;
  changes: { lane_overrides: LaneOverride[]; zones: { type: string; polygon: [number, number][] }[] };
  countermeasure: string | null;
  area: [number, number][] | null;
}

export interface ScenarioRow {
  id: string; name: string; by: string; at: string; status: "running" | "done" | "failed"; error?: string | null;
  summary: { replay_counted?: [number, number]; replay_added?: number; replay_removed?: number;
             projection_before?: number; projection_after?: { events_low: number; events_high: number } | null };
  result?: Record<string, any>;
}

/** The request for the planner's pending edits (+ an optional countermeasure over a drawn area). */
export function buildRequest(sessionId: string, name: string, overrides: LaneOverride[], zones: Zone[],
                             countermeasure: string | null, area: [number, number][] | null): { body?: ScenarioRequest; error?: string } {
  if (!sessionId) return { error: "Pick a recorded session first: the replay needs its traffic" };
  if (!name.trim()) return { error: "Name the scenario (so it can be compared later)" };
  if (!overrides.length && !zones.length && !countermeasure) return { error: "Add a lane change or zone, or pick a countermeasure" };
  return {
    body: {
      session_id: sessionId, name: name.trim(),
      changes: { lane_overrides: overrides, zones: zones.map((z) => ({ type: z.type, polygon: z.polygon })) },
      countermeasure: countermeasure || null, area: area && area.length >= 3 ? area : null,
    },
  };
}

/** The backend stores UTC; show the planner's local time. */
export const localTime = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
};
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const counts = (o: Record<string, number> | undefined) =>
  Object.entries(o ?? {}).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${esc(k)} ${n}`).join(", ") || "none";

/** One line for the saved-scenario list. */
export function summaryText(s: ScenarioRow): string {
  if (s.status === "running") return "running…";
  if (s.status === "failed") return `failed: ${s.error ?? "?"}`;
  const parts: string[] = [];
  const r = s.summary.replay_counted;
  if (r) parts.push(`rules: ${r[0]} → ${r[1]} events`);
  if (s.summary.projection_before !== undefined) {
    const a = s.summary.projection_after;
    parts.push(a ? `projected: ${s.summary.projection_before} → ${a.events_low}–${a.events_high}` : `projected: ${s.summary.projection_before} affected, not quantified`);
  }
  return parts.join(" · ") || "done";
}

/** HTML for one finished scenario's result. */
export function resultHtml(s: ScenarioRow): string {
  if (s.status !== "done" || !s.result) return `<p>${esc(summaryText(s))}</p>`;
  const r = s.result;
  let h = `<p><b>${esc(s.name)}</b> <span class="muted">by ${esc(s.by)}, ${esc(localTime(s.at))}</span></p>`;
  if (r.replay) {
    const rp = r.replay;
    h += `<p><b>Rule replay</b> <span class="tag">measured on recorded traffic</span><br>` +
      `${rp.baseline.counted} → <b>${rp.modified.counted}</b> events (+${rp.added.length} / −${rp.removed.length}), ` +
      `${rp.tracks} vehicles within ${rp.near_m} m<br>before: ${counts(rp.baseline.by_condition)}<br>after: ${counts(rp.modified.by_condition)}` +
      `<br><span class="muted">${esc(rp.note)}</span></p>`;
  }
  if (r.projection) {
    const p = r.projection;
    const after = p.projected_after ? `<b>${p.projected_after.events_low}–${p.projected_after.events_high}</b>` : "<b>not quantified</b>";
    h += `<p><b>${esc(p.countermeasure)}</b> <span class="tag">projected estimate</span><br>` +
      `affected: ${p.affected.events} events (${counts(p.affected.by_condition)})${p.before?.per_hour != null ? `, ${p.before.per_hour}/h` : ""}<br>` +
      `before ${p.before.events} → after ${after}<br><span class="muted">${esc(p.basis)}${p.source?.url ? ` · <a href="${esc(p.source.url)}" target="_blank">source</a>` : ""}</span></p>`;
  }
  return h;
}
