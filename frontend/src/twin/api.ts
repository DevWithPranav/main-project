/**
 * Data access of the in-dashboard twin. Everything the dashboard already has goes through its api
 * client (same tokens, refresh and 401 handling); the few twin-only routes (town objects, what-if
 * scenarios) use the same token store and refresh. Zones go through /api/zones (api.createZone ...),
 * not into a profile any more.
 */
import { api, refreshTokens, tokenStore } from "../api";
import { ApiError, errorDetail } from "../api/client";
import type { Profile } from "../api/types";
import type { Scene, Session, TownObjects, Trajectories, TwinEvent } from "./types";
import type { Countermeasure, ScenarioRequest, ScenarioRow } from "./whatif";

async function twinJson<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (r.status === 401 && token && !retried && (await refreshTokens())) return twinJson<T>(method, path, body, true);
  if (!r.ok) {
    let j: unknown = null;
    try {
      j = await r.json();
    } catch {
      /* not JSON */
    }
    throw new ApiError(r.status, errorDetail(j, `${r.status} ${r.statusText}`));
  }
  return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
}

const enc = encodeURIComponent;

export const twinApi = {
  scenes: () => api.scenes() as Promise<unknown[]>,
  scene: (town: string) => api.scene(town) as unknown as Promise<Scene>,
  /** A session's own map: its town, or a real clip's site map (its own metres). */
  sessionScene: (id: string) => api.sessionScene(id) as unknown as Promise<Scene>,
  /** Static town objects as oriented boxes (export_town_objects.py); 404 when not exported. */
  objects: (town: string) => twinJson<TownObjects>("GET", `/scenes/${enc(town)}/objects`),
  sessions: () => api.sessions() as unknown as Promise<Session[]>,
  session: (id: string) => api.session(id) as unknown as Promise<Session>,
  trajectories: (id: string) => api.trajectories(id) as unknown as Promise<Trajectories>,

  /** Every event of a session (pages through limit/offset). */
  async events(sessionId: string, max = 5000): Promise<TwinEvent[]> {
    const out: TwinEvent[] = [];
    for (let off = 0; off < max; off += 500) {
      const r = await api.events({ session_id: sessionId }, { limit: 500, offset: off });
      out.push(...(r.items as unknown as TwinEvent[]));
      if (out.length >= r.total || r.items.length === 0) break;
    }
    return out;
  },

  profiles: () => api.profiles() as unknown as Promise<any[]>,
  profile: (name: string) => api.profile(name) as unknown as Promise<Record<string, any>>,
  profileHistory: (name: string) => api.profileHistory(name) as unknown as Promise<any[]>,
  putProfile: (name: string, profile: Record<string, any>, note: string) =>
    api.saveProfile(name, profile as unknown as Profile, note) as Promise<{ version?: number; file?: string | null } | undefined>,

  zones: api.zones,
  createZone: api.createZone,
  updateZone: api.updateZone,
  deactivateZone: api.deactivateZone,
  zoneHistory: api.zoneHistory,

  /** What-if scenarios (PRD 21.3): POST returns at once with status "running"; poll scenario(id). */
  countermeasures: () => twinJson<Countermeasure[]>("GET", "/scenarios/countermeasures"),
  createScenario: (body: ScenarioRequest) => twinJson<ScenarioRow>("POST", "/scenarios", body),
  scenario: (id: string) => twinJson<ScenarioRow>("GET", `/scenarios/${enc(id)}`),
  scenarios: (sessionId: string) => twinJson<ScenarioRow[]>("GET", `/scenarios?session_id=${enc(sessionId)}`),

  /** WebSocket URL for live vehicles/events (token as ?token=, per the contract); read at connect time. */
  liveUrl(sessionId?: string | null): string {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const q = new URLSearchParams();
    if (sessionId) q.set("session_id", sessionId);
    const t = tokenStore.get();
    if (t) q.set("token", t);
    return `${proto}://${location.host}/api/ws/live?${q}`;
  },
};

/** The /api/profiles list may return documents or {name,...} summaries; normalise to names + map. */
export function profileSummaries(list: any[]): { name: string; map: string | null }[] {
  return (list || []).map((p) => {
    const doc = p?.profile ?? p;
    return { name: String(doc?.name ?? p?.name ?? ""), map: doc?.applies_to?.map ?? null };
  }).filter((p) => p.name);
}

/** GET /api/profiles/{name} may wrap the document ({profile, version, ...}); return the document. */
export function unwrapProfile(r: Record<string, any>): Record<string, any> {
  return r && typeof r.profile === "object" && r.profile && r.profile.profile_version ? r.profile : r;
}
