// Typed client for backend/API.md. Takes a fetch implementation so the same client runs against
// the real backend, the in-browser mock (VITE_MOCK=1) and the unit tests.

import { filtersToParams, type EventFilters, type PageOpts } from '../lib/filters';
import type {
  ConditionsDoc,
  Health,
  LoginResponse,
  Me,
  Page,
  PlannerHistoryEntry,
  Profile,
  ProfileVersion,
  Recommendation,
  ReportRecord,
  RoadAttribute,
  Scene,
  SceneListEntry,
  Session,
  SessionVideo,
  VideoKind,
  LiveSource,
  Stats,
  TrafficEvent,
  Trajectories,
  ZoneFeature,
  ZoneInput,
  ZoneVersionRow,
} from './types';

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
    this.name = 'ApiError';
  }
}

export type ExportFormat = 'pdf' | 'xlsx' | 'geojson' | 'csv';

export interface ClientOptions {
  baseUrl?: string;
  fetch: FetchLike;
  getToken: () => string | null;
  /** Called on a 401: renew the access token (POST /api/auth/refresh); true = retry the request. */
  refresh?: () => Promise<boolean>;
  onUnauthorized?: () => void;
}

/** `exp` (unix s) of a JWT, or null for anything else (e.g. the mock's tokens). */
export function tokenExp(token: string | null | undefined): number | null {
  const part = token?.split('.')[1];
  if (!part) return null;
  try {
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const exp = (JSON.parse(atob(b64 + '='.repeat((4 - (b64.length % 4)) % 4))) as { exp?: unknown }).exp;
    return typeof exp === 'number' ? exp : null;
  } catch {
    return null;
  }
}

/** The media URL to use: `prev` while it is the same file and its signed link (?exp=) has > 5 min
 * left, else `next`. Keeps a <video src> stable across refetches that re-sign the link. */
export function keepMediaUrl(prev: string | null, next: string | null, now = Date.now()): string | null {
  if (!prev || !next || prev === next || prev.split('?')[0] !== next.split('?')[0]) return next;
  const exp = Number(/[?&]exp=(\d+)/.exec(prev)?.[1] ?? 0);
  return exp * 1000 - now > 300_000 ? prev : next;
}

/** FastAPI-style `{"detail": ...}`, where detail may also be a validation-error list. */
export function errorDetail(body: unknown, fallback: string): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d))
      return d
        .map((x) => (x && typeof x === 'object' && 'msg' in x ? String((x as { msg: unknown }).msg) : JSON.stringify(x)))
        .join('; ');
    return JSON.stringify(d);
  }
  return fallback;
}

/** File name from a Content-Disposition header, else a default. */
export function filenameFrom(disposition: string | null, fallback: string): string {
  if (!disposition) return fallback;
  const star = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(disposition);
  if (star) return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''));
  const plain = /filename="?([^";]+)"?/i.exec(disposition);
  return plain ? plain[1].trim() : fallback;
}

export function createApiClient(opts: ClientOptions) {
  const base = (opts.baseUrl ?? '').replace(/\/$/, '');

  async function raw(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
    const headers = new Headers(init.headers);
    const token = opts.getToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (init.body && typeof init.body === 'string' && !headers.has('Content-Type'))
      headers.set('Content-Type', 'application/json');
    let res: Response;
    try {
      res = await opts.fetch(`${base}${path}`, { ...init, headers });
    } catch (e) {
      throw new ApiError(0, `Backend unreachable (${(e as Error).message})`);
    }
    // expired access token: refresh once and retry (not for the auth routes themselves)
    if (res.status === 401 && token && !retried && opts.refresh && !path.startsWith('/api/auth/') && (await opts.refresh()))
      return raw(path, init, true);
    if (!res.ok) {
      let body: unknown = null;
      try {
        body = await res.json();
      } catch {
        /* not JSON */
      }
      if (res.status === 401 && token) opts.onUnauthorized?.();
      throw new ApiError(res.status, errorDetail(body, `${res.status} ${res.statusText || 'error'}`));
    }
    return res;
  }

  async function json<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await raw(path, init);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  const qs = (p: URLSearchParams) => (p.toString() ? `?${p}` : '');
  const post = <T>(path: string, body: unknown) => json<T>(path, { method: 'POST', body: JSON.stringify(body) });

  return {
    login: (username: string, password: string) => post<LoginResponse>('/api/auth/login', { username, password }),
    refresh: (refresh_token: string) => post<LoginResponse>('/api/auth/refresh', { refresh_token }),
    /** Revokes the current access token and the refresh token on the server. */
    logout: (refresh_token: string | null) => json<void>('/api/auth/logout', { method: 'POST', body: JSON.stringify({ refresh_token }) }),
    me: () => json<Me>('/api/me'),
    health: () => json<Health>('/api/health'),

    sessions: () => json<Session[]>('/api/sessions'),
    session: (id: string) => json<Session>(`/api/sessions/${encodeURIComponent(id)}`),
    importSession: (body: { flight: string; violations_dir?: string; scene?: string; profile?: string }) =>
      post<Session>('/api/sessions/import', body),
    trajectories: (id: string) => json<Trajectories>(`/api/sessions/${encodeURIComponent(id)}/trajectories`),
    sessionVideo: (id: string, kind: VideoKind = 'annotated') =>
      json<SessionVideo>(`/api/sessions/${encodeURIComponent(id)}/video?kind=${kind}`),
    makeSessionVideo: (id: string, kind: VideoKind = 'annotated') =>
      post<SessionVideo>(`/api/sessions/${encodeURIComponent(id)}/video?kind=${kind}`, {}),
    liveSources: () => json<LiveSource[]>('/api/live/sources'),

    events: (f: EventFilters, page: PageOpts = {}) =>
      json<Page<TrafficEvent>>(`/api/events${qs(filtersToParams(f, page))}`),
    event: (id: string) => json<TrafficEvent>(`/api/events/${encodeURIComponent(id)}`),
    review: (id: string, outcome: 'confirmed' | 'dismissed', note?: string) =>
      post<TrafficEvent>(`/api/events/${encodeURIComponent(id)}/review`, note ? { outcome, note } : { outcome }),

    stats: (f: EventFilters) => json<Stats>(`/api/stats${qs(filtersToParams(f))}`),
    exportUrl: (format: ExportFormat, f: EventFilters) => {
      const p = filtersToParams(f);
      p.set('format', format);
      return `${base}/api/export?${p}`;
    },
    /** Downloads with the auth header (a plain link could not send it). */
    exportFile: async (format: ExportFormat, f: EventFilters): Promise<{ blob: Blob; filename: string }> => {
      const p = filtersToParams(f);
      p.set('format', format);
      const res = await raw(`/api/export?${p}`);
      return { blob: await res.blob(), filename: filenameFrom(res.headers.get('Content-Disposition'), `events.${format}`) };
    },
    /** Every export made so far, newest first (backend keeps the files in S3). */
    reports: (limit = 50) => json<Page<ReportRecord>>(`/api/reports?limit=${limit}`),
    reportFile: async (r: ReportRecord): Promise<{ blob: Blob; filename: string }> => {
      const res = await raw(`/api/reports/${encodeURIComponent(r.id)}`);
      return { blob: await res.blob(), filename: filenameFrom(res.headers.get('Content-Disposition'), r.filename) };
    },

    profiles: () => json<(Profile | ProfileVersion)[]>('/api/profiles'),
    profile: (name: string) => json<Profile | ProfileVersion>(`/api/profiles/${encodeURIComponent(name)}`),
    saveProfile: (name: string, profile: Profile, note: string) =>
      json<unknown>(`/api/profiles/${encodeURIComponent(name)}`, {
        method: 'PUT',
        body: JSON.stringify({ profile, note }),
      }),
    profileHistory: (name: string) => json<ProfileVersion[]>(`/api/profiles/${encodeURIComponent(name)}/history`),

    conditions: () => json<ConditionsDoc>('/api/conditions'),
    scenes: () => json<SceneListEntry[]>('/api/scenes'),
    scene: (town: string) => json<Scene>(`/api/scenes/${encodeURIComponent(town)}`),
    /** The town as a profile runs it: its road.lane_overrides applied, changed lanes carry `overridden`. */
    sceneForProfile: (town: string, profile: string) =>
      json<Scene>(`/api/scenes/${encodeURIComponent(town)}?profile=${encodeURIComponent(profile)}`),
    /** What a lane override may set and which violation conditions each attribute drives. */
    roadAttributes: () => json<RoadAttribute[]>('/api/road/attributes'),
    /** The map a session lives in: its town, or a real clip's own site map (metres of that site). */
    sessionScene: (sid: string) => json<Scene>(`/api/sessions/${encodeURIComponent(sid)}/scene`),

    /** Zones of a scene / site; withStatic adds the scene file's own (read-only) zones. */
    zones: (scene: string, opts: { withStatic?: boolean; active?: 'true' | 'false' | 'all' } = {}) =>
      json<{ features: ZoneFeature[] }>(`/api/zones${qs(new URLSearchParams({
        scene, active: opts.active ?? 'true', ...(opts.withStatic ? { include_static: 'true' } : {}),
      }))}`).then((r) => r.features),
    createZone: (z: ZoneInput) => post<ZoneFeature>('/api/zones', z),
    updateZone: (id: string, z: ZoneInput) =>
      json<ZoneFeature>(`/api/zones/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(z) }),
    deactivateZone: (id: string, note?: string) =>
      json<ZoneFeature>(`/api/zones/${encodeURIComponent(id)}${note ? `?note=${encodeURIComponent(note)}` : ''}`, { method: 'DELETE' }),
    zoneHistory: (id: string) => json<ZoneVersionRow[]>(`/api/zones/${encodeURIComponent(id)}/history`),

    recommendations: (sessionId?: string | null) =>
      json<Recommendation[] | Page<Recommendation>>(`/api/recommendations${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''}`),
    decide: (id: string, decision: 'accepted' | 'rejected' | 'modified', rationale: string) =>
      post<unknown>(`/api/recommendations/${encodeURIComponent(id)}/decision`, { decision, rationale }),
    plannerHistory: () => json<PlannerHistoryEntry[] | Page<PlannerHistoryEntry>>('/api/planner/history'),
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

/** Lists may come bare or as {total, items}. */
export function asList<T>(x: T[] | Page<T> | null | undefined): T[] {
  if (!x) return [];
  return Array.isArray(x) ? x : (x.items ?? []);
}
