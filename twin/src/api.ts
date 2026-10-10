/**
 * Backend client (backend/API.md). The JWT lives in memory only (never localStorage).
 * Base "/api" goes through the Vite proxy to http://localhost:8000. With ?mock=1 the twin reads
 * "/mock-api", a read-only dev middleware (dev/mockApi.ts) that serves the lane maps and a recorded
 * flight's trajectories/events from the repo in the same shapes, so the twin runs without a backend.
 */
import type { Scene, Session, Trajectories, TwinEvent } from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export class Api {
  token: string | null = null;
  role: string | null = null;
  username: string | null = null;

  constructor(public base = "/api") {}

  get mock(): boolean {
    return this.base !== "/api";
  }

  private async req<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    const r = await fetch(this.base + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
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
    const r = await this.req<{ access_token: string; role: string }>("POST", "/auth/login", { username, password });
    this.token = r.access_token;
    this.role = r.role;
    this.username = username;
    return r.role;
  }

  logout(): void {
    this.token = this.role = this.username = null;
  }

  canEdit(): boolean {
    return this.role === "PLANNER" || this.role === "ADMIN";
  }

  health = () => this.req<Record<string, string>>("GET", "/health");
  scenes = () => this.req<unknown>("GET", "/scenes");
  scene = (town: string) => this.req<Scene>("GET", `/scenes/${encodeURIComponent(town)}`);
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
