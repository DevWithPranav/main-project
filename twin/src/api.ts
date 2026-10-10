/**
 * Backend client (backend/API.md). The JWT lives in memory only (never localStorage).
 * Base "/api" goes through the Vite proxy to http://localhost:8000. With ?mock=1 the twin reads
 * "/mock-api", a read-only dev middleware (dev/mockApi.ts) that serves the lane maps and a recorded
 * flight's trajectories/events from the repo in the same shapes, so the twin runs without a backend.
 */
import type { Scene, Session, TownObjects, Trajectories, TwinEvent } from "./types";
import type { Countermeasure, ScenarioRequest, ScenarioRow } from "./whatif";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** `exp` (unix s) of a JWT, else null. */
export function tokenExp(token: string | null): number | null {
  const part = token?.split(".")[1];
  if (!part) return null;
  try {
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const exp = JSON.parse(atob(b64 + "=".repeat((4 - (b64.length % 4)) % 4))).exp;
    return typeof exp === "number" ? exp : null;
  } catch {
    return null;
  }
}

export class Api {
  token: string | null = null;
  /** Standalone login only; embedded in the dashboard the parent sends refreshed access tokens. */
  refreshToken: string | null = null;
  role: string | null = null;
  username: string | null = null;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private refreshing: Promise<boolean> | null = null;

  constructor(public base = "/api") {}

  get mock(): boolean {
    return this.base !== "/api";
  }

  /** New tokens: renew a minute before the access token expires (keeps the live socket's token valid). */
  setTokens(access: string | null, refresh: string | null = this.refreshToken): void {
    this.token = access;
    this.refreshToken = refresh;
    clearTimeout(this.timer);
    const exp = tokenExp(access);
    if (exp !== null && refresh) this.timer = setTimeout(() => void this.refresh(), Math.max(5000, exp * 1000 - Date.now() - 60000));
  }

  /** POST /auth/refresh (one at a time); false when there is no refresh token or it was refused. */
  refresh(): Promise<boolean> {
    this.refreshing ??= (async () => {
      if (!this.refreshToken) return false;
      try {
        const r = await this.req<{ access_token: string; refresh_token: string }>("POST", "/auth/refresh", { refresh_token: this.refreshToken });
        this.setTokens(r.access_token, r.refresh_token);
        return true;
      } catch {
        return false;
      }
    })().finally(() => (this.refreshing = null));
    return this.refreshing;
  }

  private async req<T>(method: string, path: string, body?: unknown, retried = false): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const r = await fetch(this.base + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (r.status === 401 && this.token && !retried && !path.startsWith("/auth/") && (await this.refresh()))
      return this.req<T>(method, path, body, true);
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try {
        const j = await r.json();
        if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch {
        /* not JSON */
      }
      throw new ApiError(r.status, msg);
    }
    return r.status === 204 ? (undefined as T) : ((await r.json()) as T);
  }

  async login(username: string, password: string): Promise<string> {
    const r = await this.req<{ access_token: string; refresh_token?: string; role: string }>("POST", "/auth/login", { username, password });
    this.setTokens(r.access_token, r.refresh_token ?? null);
    this.role = r.role;
    this.username = username;
    return r.role;
  }

  /** Revokes both tokens on the server (best effort) and forgets them. */
  async logout(): Promise<void> {
    if (this.token || this.refreshToken)
      await this.req("POST", "/auth/logout", { refresh_token: this.refreshToken }).catch(() => undefined);
    this.setTokens(null, null);
    this.role = this.username = null;
  }

  canEdit(): boolean {
    return this.role === "PLANNER" || this.role === "ADMIN";
  }

  health = () => this.req<Record<string, string>>("GET", "/health");
  scenes = () => this.req<unknown>("GET", "/scenes");
  scene = (town: string) => this.req<Scene>("GET", `/scenes/${encodeURIComponent(town)}`);
  /** A session's own map: its town, or a real clip's site map (its own metres). */
  sessionScene = (id: string) => this.req<Scene>("GET", `/sessions/${encodeURIComponent(id)}/scene`);
  /** Static town objects as oriented boxes (export_town_objects.py); 404 when not exported. */
  objects = (town: string) => this.req<TownObjects>("GET", `/scenes/${encodeURIComponent(town)}/objects`);
  sessions = () => this.req<Session[]>("GET", "/sessions");
  session = (id: string) => this.req<Session>("GET", `/sessions/${encodeURIComponent(id)}`);
  trajectories = (id: string) => this.req<Trajectories>("GET", `/sessions/${encodeURIComponent(id)}/trajectories`);

  /** Every event of a session (pages through limit/offset). */
  async events(sessionId: string, max = 5000): Promise<TwinEvent[]> {
    const out: TwinEvent[] = [];
    for (let off = 0; off < max; off += 500) {
      const r = await this.req<{ total: number; items: TwinEvent[] }>("GET", `/events?session_id=${encodeURIComponent(sessionId)}&limit=500&offset=${off}`);
      out.push(...r.items);
      if (out.length >= r.total || r.items.length === 0) break;
    }
    return out;
  }

  profiles = () => this.req<any[]>("GET", "/profiles");
  profile = (name: string) => this.req<Record<string, any>>("GET", `/profiles/${encodeURIComponent(name)}`);
  profileHistory = (name: string) => this.req<any[]>("GET", `/profiles/${encodeURIComponent(name)}/history`);
  putProfile = (name: string, body: { profile: unknown; note: string; zones?: unknown }) =>
    this.req<unknown>("PUT", `/profiles/${encodeURIComponent(name)}`, body);

  /** What-if scenarios (PRD 21.3): POST returns at once with status "running"; poll scenario(id). */
  countermeasures = () => this.req<Countermeasure[]>("GET", "/scenarios/countermeasures");
  createScenario = (body: ScenarioRequest) => this.req<ScenarioRow>("POST", "/scenarios", body);
  scenario = (id: string) => this.req<ScenarioRow>("GET", `/scenarios/${encodeURIComponent(id)}`);
  scenarios = (sessionId: string) => this.req<ScenarioRow[]>("GET", `/scenarios?session_id=${encodeURIComponent(sessionId)}`);

  /** WebSocket URL for live vehicles/events (token as ?token=, per the contract). */
  liveUrl(sessionId?: string): string {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const q = new URLSearchParams();
    if (sessionId) q.set("session_id", sessionId);
    if (this.token) q.set("token", this.token);
    return `${proto}://${location.host}${this.base}/ws/live?${q}`;
  }
}

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
