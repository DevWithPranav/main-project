/**
 * The signals and stop signs of a scenario plan on the map, with their state now (pure, no Cesium; unit tested).
 *
 * Where they act mirrors the virtual traffic model (ml/planning/microsim.py): a signal on a junction's approaches is
 * one controller for that junction (its approach lanes stop 1 m before their end, green by road axis); a signal placed
 * on a road controls the lanes passing within SIGNAL_REACH_M, stopping STOP_BACK_M before the position. The state
 * comes from the simulated run when the Custom traffic has one ("simulated"), else from the signal's own timing
 * ("planned": A green for green_s, 2 s clearance, B green for the rest of the cycle, 2 s clearance; an adaptive signal
 * stretches its greens with traffic, so its planned timing is only the base plan).
 */
import type { PlanItem, VirtualSignal } from "../api/types";
import type { Lane, Scene } from "./types";

export const SIGNAL_REACH_M = 14; // microsim.SIGNAL_REACH_M
export const STOP_BACK_M = 3; // microsim.STOP_BACK_M
export const CLEARANCE_S = 2; // microsim.CLEARANCE_S
const KINDS = new Set(["traffic_signal", "stop_sign"]);

export interface Head {
  x: number;
  y: number;
  group: "A" | "B";
  /** stop line across the lane (CARLA metres) */
  bar: [[number, number], [number, number]];
}

export interface PlanSignal {
  /** the first item's id (a junction controller covers several items) */
  id: string;
  kind: "traffic_signal" | "stop_sign";
  x: number;
  y: number;
  heads: Head[];
  cycle: number;
  green: number;
  adaptive: boolean;
  /** the junction it controls, if any */
  junction?: string;
}

/** Light colour of a head and the controller's state text. */
export interface SignalView {
  colour: "green" | "yellow" | "red" | "stop" | "off";
  phase: string; // "A", "B", "R" (clearance), "ALL" (off / flashing)
}

// ------------------------------------------------------------------ lane geometry

interface Proj {
  d: number; // distance from the point to the lane
  s: number; // arc length of the nearest point
}

function cum(c: [number, number][]): number[] {
  const out = [0];
  for (let i = 1; i < c.length; i++) out.push(out[i - 1] + Math.hypot(c[i][0] - c[i - 1][0], c[i][1] - c[i - 1][1]));
  return out;
}

function project(c: [number, number][], cs: number[], x: number, y: number): Proj {
  let best: Proj = { d: Infinity, s: 0 };
  for (let i = 0; i + 1 < c.length; i++) {
    const [ax, ay] = c[i], [bx, by] = c[i + 1];
    const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    const t = l2 === 0 ? 0 : Math.min(Math.max(((x - ax) * dx + (y - ay) * dy) / l2, 0), 1);
    const d = Math.hypot(x - (ax + t * dx), y - (ay + t * dy));
    if (d < best.d) best = { d, s: cs[i] + t * Math.sqrt(l2) };
  }
  return best;
}

/** Position and heading (rad, atan2 in CARLA axes) at arc length s. */
function at(c: [number, number][], cs: number[], s: number): { x: number; y: number; h: number } {
  const L = cs[cs.length - 1];
  s = Math.min(Math.max(s, 0), L);
  let i = 1;
  while (i < cs.length - 1 && cs[i] < s) i++;
  const [ax, ay] = c[i - 1], [bx, by] = c[i];
  const t = cs[i] > cs[i - 1] ? (s - cs[i - 1]) / (cs[i] - cs[i - 1]) : 0;
  return { x: ax + (bx - ax) * t, y: ay + (by - ay) * t, h: Math.atan2(by - ay, bx - ax) };
}

function head(lane: Lane, cs: number[], s: number, group: "A" | "B"): Head {
  const p = at(lane.centreline, cs, s);
  const w = (lane.width_m ?? 3.5) / 2;
  const nx = -Math.sin(p.h), ny = Math.cos(p.h);
  return { x: p.x, y: p.y, group, bar: [[p.x + nx * w, p.y + ny * w], [p.x - nx * w, p.y - ny * w]] };
}

const axisDiff = (a: number, b: number) => Math.abs((((a - b + 90) % 180) + 180) % 180 - 90);
const deg = (r: number) => ((r * 180) / Math.PI + 360) % 180;

// ------------------------------------------------------------------ where the plan's signals act

/** The plan's signals and stop signs as the virtual model places them (items without a match are left out). */
export function planSignals(items: PlanItem[], scene: Scene | null): PlanSignal[] {
  if (!scene) return [];
  const lanes = scene.lanes.filter((l) => l.centreline.length >= 2 && (l.lane_type ?? "driving") === "driving");
  const byId = new Map(lanes.map((l) => [l.id, l]));
  const cums = new Map(lanes.map((l) => [l.id, cum(l.centreline)]));
  const out: PlanSignal[] = [];
  const atJunction = new Map<string, PlanItem[]>();
  for (const it of items) {
    if (!KINDS.has(it.kind)) continue;
    const jid = it.target?.junction_id;
    if (jid) {
      const k = `${it.kind}|${jid}`;
      atJunction.set(k, [...(atJunction.get(k) ?? []), it]);
      continue;
    }
    // a signal on a road: the lanes passing within reach, stop line STOP_BACK_M before the position
    const cand: { lane: Lane; s: number; d: number; ax: number }[] = [];
    for (const l of lanes) {
      const cs = cums.get(l.id)!;
      const p = project(l.centreline, cs, it.x, it.y);
      if (p.d <= SIGNAL_REACH_M && p.s - STOP_BACK_M >= 4) cand.push({ lane: l, s: p.s - STOP_BACK_M, d: p.d, ax: deg(at(l.centreline, cs, p.s).h) });
    }
    if (!cand.length) continue;
    const ref = cand.reduce((a, b) => (b.d < a.d ? b : a)).ax;
    out.push({
      ...base(it), x: it.x, y: it.y,
      heads: cand.map((c) => head(c.lane, cums.get(c.lane.id)!, c.s, it.kind !== "traffic_signal" || axisDiff(c.ax, ref) < 40 ? "A" : "B")),
    });
  }
  for (const its of atJunction.values()) {
    const j = scene.junctions?.find((q) => q.id === its[0].target!.junction_id);
    if (!j) continue;
    const want = new Set<string>();
    for (const it of its) {
      if (it.target?.approach_id) want.add(it.target.approach_id);
      else j.approaches.forEach((a) => want.add(a.id));
    }
    const heads: Head[] = [];
    let ref: number | null = null;
    for (const a of j.approaches) {
      if (!want.has(a.id)) continue;
      const ax = ((a.heading_deg % 180) + 180) % 180;
      ref ??= ax;
      const group = its[0].kind !== "traffic_signal" || axisDiff(ax, ref) < 40 ? "A" : "B";
      for (const lid of a.lanes) {
        const l = byId.get(lid);
        if (!l) continue;
        const cs = cums.get(lid)!;
        heads.push(head(l, cs, Math.max(cs[cs.length - 1] - 1, 0.5), group));
      }
    }
    if (heads.length) out.push({ ...base(its[0]), x: j.centre[0], y: j.centre[1], heads, junction: j.id });
  }
  return out;
}

function base(it: PlanItem) {
  const p = it.params ?? {};
  return {
    id: it.id, kind: it.kind as PlanSignal["kind"],
    cycle: Number(p.cycle_s ?? 90), green: Number(p.green_s ?? 40), adaptive: Boolean(p.adaptive),
  };
}

// ------------------------------------------------------------------ state over time

/** A fixed-time controller's timeline from t = 0: [[t, "A" | "R" | "B"], ...] for one cycle (microsim.Signal). */
export function plannedTimeline(cycle: number, green: number): { tl: [number, string][]; period: number } {
  const b = Math.max(cycle - green - 2 * CLEARANCE_S, 5);
  const tl: [number, string][] = [[0, "A"], [green, "R"], [green + CLEARANCE_S, "B"], [green + CLEARANCE_S + b, "R"]];
  return { tl, period: green + b + 2 * CLEARANCE_S };
}

/** Phase at t, the phase before it (for the clearance after a green) and seconds until the next change. */
export function phaseAt(tl: [number, string][], t: number, period: number | null = null): { phase: string; prev: string | null; left: number | null } {
  if (!tl.length) return { phase: "ALL", prev: null, left: null };
  const tt = period ? ((t % period) + period) % period : t;
  let k = -1;
  for (let i = 0; i < tl.length; i++) if (tl[i][0] <= tt) k = i;
  if (k < 0) return { phase: tl[0][1], prev: null, left: tl[0][0] - tt };
  const next = k + 1 < tl.length ? tl[k + 1][0] : period ? period : null;
  const prev = k > 0 ? tl[k - 1][1] : period ? tl[tl.length - 1][1] : null;
  return { phase: tl[k][1], prev, left: next === null ? null : next - tt };
}

/** A head's colour in a phase: its group's green, yellow in the clearance right after its green (all red in the model), else red. */
export function headColour(kind: PlanSignal["kind"] | VirtualSignal["kind"], group: string, phase: string, prev: string | null): SignalView["colour"] {
  if (kind === "stop_sign") return "stop";
  if (phase === "ALL") return "off";
  if (phase === group) return "green";
  if (phase === "R" && prev === group) return "yellow";
  return "red";
}

/** "A green 12 s · B red" style text for a controller. */
export function stateText(kind: PlanSignal["kind"], phase: string, prev: string | null, left: number | null, groups: Set<string>): string {
  if (kind === "stop_sign") return "stop";
  if (phase === "ALL") return "off";
  const secs = left === null ? "" : ` ${Math.max(0, Math.ceil(left))} s`;
  const parts = [...groups].sort().map((g) => {
    const c = headColour(kind, g, phase, prev);
    return `${groups.size > 1 ? g + " " : ""}${c}${c !== "red" || phase === "R" ? secs : ""}`;
  });
  return parts.join(" · ");
}

export const SIGNAL_CSS: Record<SignalView["colour"], string> = {
  green: "#40c057", yellow: "#fab005", red: "#fa5252", stop: "#fd7e14", off: "#868e96",
};
