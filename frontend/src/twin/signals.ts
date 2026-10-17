/**
 * CARLA's own traffic lights in the twin (pure, no Cesium; unit tested).
 *
 * Replay: GET /api/sessions/{id}/signals gives each light's stop lines and a timeline [[t, state], ...] on the
 * session's time axis. Live: the WebSocket's {"type": "signals"} messages (a snapshot of every light's current
 * state when the socket opens, then each change) carry CARLA's sim time, the clock of the vehicles' t_s. The
 * twin shows each light as it was at the time of the vehicles on screen, so network and detector delay do not
 * put the lights ahead of the cars (services/live/pipeline.py, backend/app/live.py).
 */

export interface SignalStopLine {
  id: string;
  signal_id?: string;
  line: [number, number][];
  dir?: [number, number];
}

export interface SignalLight {
  id: string;
  actor_id?: number;
  stop_lines: SignalStopLine[];
}

export interface SessionSignals {
  lights: SignalLight[];
  timelines: Record<string, [number, string][]>;
  time_base: string | null;
}

/** Live: how much history each light keeps (seconds of sim time before its latest change). */
export const LIVE_KEEP_S = 600;

export const SIGNAL_COLOR: Record<string, string> = { Red: "#fa5252", Yellow: "#fab005", Green: "#40c057" };
export const SIGNAL_UNKNOWN = "#868e96"; // no state yet, Off, Unknown
export const signalColor = (state: string | null | undefined) => (state && SIGNAL_COLOR[state]) || SIGNAL_UNKNOWN;

export class SignalStore {
  readonly lights = new Map<string, SignalLight>();
  private readonly tl = new Map<string, [number, string][]>(); // sorted by t
  /** bumps whenever the light list changes (the 3D layer rebuilds its lines) */
  version = 0;
  latestT = -Infinity;

  static fromSession(doc: SessionSignals | null | undefined): SignalStore | null {
    if (!doc || !doc.lights?.length) return null;
    const s = new SignalStore();
    s.setLights(doc.lights);
    for (const [id, tl] of Object.entries(doc.timelines ?? {})) for (const [t, st] of tl) s.add(t, id, st);
    return s;
  }

  setLights(lights: SignalLight[]) {
    this.lights.clear();
    for (const l of lights) this.lights.set(String(l.id), l);
    this.version++;
  }

  /** A state change at t; repeats and out-of-order arrivals are fine (kept sorted, exact duplicates dropped). */
  add(t: number, id: string, state: string) {
    const key = String(id);
    let a = this.tl.get(key);
    if (!a) this.tl.set(key, (a = []));
    let i = a.length;
    while (i > 0 && a[i - 1][0] > t) i--;
    if (i > 0 && a[i - 1][0] === t && a[i - 1][1] === state) return;
    a.splice(i, 0, [t, state]);
    if (t > this.latestT) this.latestT = t;
  }

  /** Drop history older than keepS before each light's latest change (its state then is kept). */
  trim(keepS = LIVE_KEEP_S) {
    for (const a of this.tl.values()) {
      const last = a[a.length - 1]?.[0];
      if (last === undefined) continue;
      let k = 0;
      while (k + 1 < a.length && a[k + 1][0] < last - keepS) k++;
      if (k > 0) a.splice(0, k);
    }
  }

  /** A light's state at t (the last change at or before it); t null = its latest state; null if none yet. */
  stateAt(id: string, t: number | null): string | null {
    const a = this.tl.get(String(id));
    if (!a?.length) return null;
    if (t === null) return a[a.length - 1][1];
    let lo = 0, hi = a.length - 1, k = -1;
    while (lo <= hi) {
      const m = (lo + hi) >> 1;
      if (a[m][0] <= t) {
        k = m;
        lo = m + 1;
      } else hi = m - 1;
    }
    return k < 0 ? null : a[k][1];
  }

  /** How many lights are in each state at t ("none" = no state yet). */
  countsAt(t: number | null): Record<string, number> {
    const out: Record<string, number> = {};
    for (const id of this.lights.keys()) {
      const st = this.stateAt(id, t) ?? "none";
      out[st] = (out[st] ?? 0) + 1;
    }
    return out;
  }

  /** The lights with a stop line within `radius` m of (x, y) (CARLA metres). */
  lightsNear(x: number, y: number, radius: number): SignalLight[] {
    return [...this.lights.values()].filter((l) => l.stop_lines.some((sl) => {
      const [a, b] = [sl.line[0], sl.line[sl.line.length - 1]];
      return Math.hypot((a[0] + b[0]) / 2 - x, (a[1] + b[1]) / 2 - y) <= radius;
    }));
  }
}

/** The time of the vehicles on the live map: their newest t_s (sim time); null without any (latest states). */
export function liveTime(vehicles: { t_s?: number }[]): number | null {
  let t = -Infinity;
  for (const v of vehicles) if (typeof v.t_s === "number" && v.t_s > t) t = v.t_s;
  return Number.isFinite(t) ? t : null;
}

/** "12 lights · 5 red · 1 yellow · 6 green" */
export function countsText(n: number, c: Record<string, number>): string {
  const parts = [["Red", "red"], ["Yellow", "yellow"], ["Green", "green"]].filter(([k]) => c[k]).map(([k, w]) => `${c[k]} ${w}`);
  return `${n} light${n === 1 ? "" : "s"}${parts.length ? " · " + parts.join(" · ") : ""}`;
}
