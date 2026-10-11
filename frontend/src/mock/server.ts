// In-browser mock of backend/API.md, exposed as a fetch-compatible function. Used when VITE_MOCK=1
// (npm run dev:mock) so the dashboard can be built and demoed without the backend. Synthetic data.

import defaultProfile from '../../../ml/violation_engine/configs/profiles/default.json';
import town05Profile from '../../../ml/violation_engine/configs/profiles/town05.json';
import { matchesFilters, paramsToFilters } from '../lib/filters';
import { validateProfile } from '../lib/profiles';
import type {
  PlannerHistoryEntry,
  Profile,
  Recommendation,
  Role,
  Scene,
  TrafficEvent,
  VehicleState,
} from '../api/types';
import { eventTime, isAnomaly } from '../api/types';
import { CONDITIONS, generateSession, syntheticScene, type MockSessionData } from './generate';

const USERS: Record<string, Role> = {
  officer: 'OFFICER',
  operator: 'OPERATOR',
  planner: 'PLANNER',
  maintenance: 'MAINTENANCE',
  admin: 'ADMIN',
};
const TOWNS = ['Town03', 'Town04', 'Town05'];

interface ProfileRow {
  version: number;
  by: string;
  at: string;
  note: string;
  profile: Profile;
}

interface State {
  sessions: Map<string, MockSessionData>;
  profiles: Map<string, ProfileRow[]>;
  recommendations: Recommendation[];
  history: PlannerHistoryEntry[];
}

const sceneCache = new Map<string, Promise<Scene>>();
const realFetch = globalThis.fetch.bind(globalThis);

export function loadScene(town: string): Promise<Scene> {
  if (!sceneCache.has(town)) {
    sceneCache.set(
      town,
      realFetch(`/__repo/scenes/${town}.json`)
        .then((r) => (r.ok ? (r.json() as Promise<Scene>) : Promise.reject(new Error('no scene'))))
        .catch(() => syntheticScene(town)),
    );
  }
  return sceneCache.get(town)!;
}

let statePromise: Promise<State> | null = null;

function initState(): Promise<State> {
  statePromise ??= (async () => {
    const t05 = await loadScene('Town05');
    const t03 = await loadScene('Town03');
    const sessions = new Map<string, MockSessionData>();
    const a = generateSession({ id: 'mock_20261009_town05', name: 'Mock flight A (Town05)', source: 'carla', scene: t05, seed: 7, startedAt: '2026-10-09T09:12:00Z', nTracks: 60 });
    const b = generateSession({ id: 'mock_20261010_town03', name: 'Mock flight B (Town03)', source: 'carla', scene: t03, seed: 11, startedAt: '2026-10-10T15:40:00Z', nTracks: 40 });
    const live = generateSession({ id: 'mock_live', name: 'Mock live feed (Town05)', source: 'live', scene: t05, seed: 23, startedAt: new Date().toISOString(), nTracks: 50, durationS: 180 });
    for (const s of [a, b, live]) sessions.set(s.session.session_id, s);
    const now = new Date().toISOString();
    const profiles = new Map<string, ProfileRow[]>([
      ['default', [{ version: 1, by: 'seed', at: now, note: 'seeded from configs/profiles/default.json', profile: defaultProfile as unknown as Profile }]],
      ['town05', [{ version: 1, by: 'seed', at: now, note: 'seeded from configs/profiles/town05.json', profile: town05Profile as unknown as Profile }]],
    ]);
    const recommendations: Recommendation[] = [
      {
        id: 'rec-mock-1',
        type: 'speeding',
        problem: 'Cluster of speeding events on one road section (mock)',
        location: { lane_id: 'r37_s0_l-1', x: -46.4, y: -199.2 },
        evidence: { events: 12, window: 'mock session A' },
        action: 'Lower the speed limit from 50 to 40 km/h and add a speed sign',
        expected_impact: 'Projected estimate (mock): fewer speeding events on the section',
        priority: 'high',
        validation_method: 'CARLA baseline vs modified run (same seed, weather, duration)',
        confidence: 0.7,
        limitations: ['Mock data: not a measured result'],
        alternatives: ['Traffic calming', 'Enforcement camera'],
        status: 'proposed',
      },
      {
        id: 'rec-mock-2',
        type: 'illegal_stopping',
        problem: 'Repeated stops on the bridge carriageway (mock)',
        location: { zone_id: 'road_37' },
        evidence: { events: 5 },
        action: 'Mark a no-stopping zone and add a lay-by after the bridge',
        expected_impact: 'Projected estimate (mock)',
        priority: 'medium',
        validation_method: 'CARLA baseline vs modified run',
        confidence: 0.55,
        limitations: 'Mock data',
        alternatives: ['Signage only'],
        status: 'proposed',
      },
      {
        id: 'rec-mock-3',
        type: 'wrong_way',
        problem: 'Wrong-way entries at a one-way ramp (mock)',
        location: { lane_id: 'r12_s0_l1' },
        action: 'Add "No entry" signal and a restricted-direction marking',
        priority: 'low',
        validation_method: 'CARLA baseline vs modified run',
        confidence: 0.4,
        status: 'proposed',
      },
    ];
    return { sessions, profiles, recommendations, history: [] };
  })();
  return statePromise;
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const err = (status: number, detail: string) => json({ detail }, status);

function userOf(init?: RequestInit): { username: string; role: Role } | null {
  const h = new Headers(init?.headers);
  const auth = h.get('Authorization') ?? '';
  const m = /^Bearer mock\.(\w+)$/.exec(auth);
  if (!m || !USERS[m[1]]) return null;
  return { username: m[1], role: USERS[m[1]] };
}

function allEvents(s: State) {
  return [...s.sessions.values()].flatMap((d) => d.events);
}

function strip(e: TrafficEvent & { _at?: string }): TrafficEvent {
  const { _at, ...rest } = e;
  void _at;
  return rest as TrafficEvent;
}

function filtered(s: State, q: URLSearchParams) {
  const f = paramsToFilters(q);
  return allEvents(s).filter((e) => matchesFilters({ ...e, at: e._at }, f));
}

function count(xs: string[]) {
  const m: Record<string, number> = {};
  for (const x of xs) m[x] = (m[x] ?? 0) + 1;
  return m;
}

function stats(events: (TrafficEvent & { _at: string })[]) {
  const by_hour: Record<string, number> = {};
  for (const e of events) {
    const h = String(new Date(e._at).getUTCHours()).padStart(2, '0');
    by_hour[h] = (by_hour[h] ?? 0) + 1;
  }
  // Mock hotspots: 50 m grid cells with the most events (the real backend uses its own method).
  const cells = new Map<string, { x: number; y: number; count: number; types: string[] }>();
  for (const e of events) {
    const k = `${Math.floor(e.x / 50)},${Math.floor(e.y / 50)}`;
    const c = cells.get(k) ?? { x: 0, y: 0, count: 0, types: [] };
    c.x += e.x;
    c.y += e.y;
    c.count += 1;
    c.types.push(e.type);
    cells.set(k, c);
  }
  const hotspots = [...cells.values()]
    .map((c) => {
      const top = Object.entries(count(c.types)).sort((a, b) => b[1] - a[1])[0][0];
      return { x: +(c.x / c.count).toFixed(1), y: +(c.y / c.count).toFixed(1), count: c.count, type: top, radius_m: 25 };
    })
    .sort((a, b) => b.count - a.count)
    .slice(0, 10)
    .map((h, i) => ({ ...h, label: `Hotspot ${i + 1}` }));
  return {
    by_type: count(events.map((e) => e.type)),
    by_condition: count(events.filter((e) => !isAnomaly(e) && e.condition).map((e) => (e as { condition: string }).condition)),
    by_status: count(events.map((e) => e.status)),
    by_hour,
    hotspots,
  };
}

function exportBody(format: string, events: TrafficEvent[]): { body: string; type: string } {
  if (format === 'geojson')
    return {
      type: 'application/geo+json',
      body: JSON.stringify({
        type: 'FeatureCollection',
        features: events.map((e) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [e.x, e.y] }, properties: { ...e } })),
      }),
    };
  const cols = ['event_id', 'session_id', 'kind', 'type', 'condition', 'status', 'time_s', 'x', 'y', 'confidence', 'review'];
  const rows = events.map((e) =>
    [e.event_id, e.session_id, e.kind ?? 'violation', e.type, isAnomaly(e) ? '' : (e.condition ?? ''), e.status, eventTime(e), e.x, e.y, e.confidence, e.review?.outcome ?? '']
      .map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`)
      .join(','),
  );
  const csv = [cols.join(','), ...rows].join('\n');
  if (format === 'csv') return { type: 'text/csv', body: csv };
  // pdf/xlsx are the backend's job; the mock returns the CSV with a note so the flow can be tested.
  return { type: 'text/plain', body: `MOCK ${format.toUpperCase()} export (the real file comes from the backend)\n\n${csv}` };
}

/** Mock: fetch-compatible handler for `/api/...`. */
export async function mockFetch(input: string, init?: RequestInit): Promise<Response> {
  await new Promise((r) => setTimeout(r, 120 + Math.random() * 150));
  const s = await initState();
  const url = new URL(input, 'http://mock');
  const path = url.pathname;
  const method = (init?.method ?? 'GET').toUpperCase();
  const body = init?.body ? JSON.parse(String(init.body)) : null;
  const q = url.searchParams;

  if (path === '/api/auth/login' && method === 'POST') {
    const { username, password } = body ?? {};
    if (!USERS[username] || password !== `${username}123`) return err(401, 'Invalid username or password');
    return json({ access_token: `mock.${username}`, refresh_token: `mockrefresh.${username}`, role: USERS[username] });
  }
  if (path === '/api/auth/refresh' && method === 'POST') {
    const name = /^mockrefresh\.(\w+)$/.exec(String(body?.refresh_token ?? ''))?.[1];
    if (!name || !USERS[name]) return err(401, 'invalid, expired or revoked refresh token');
    return json({ access_token: `mock.${name}`, refresh_token: `mockrefresh.${name}`, role: USERS[name] });
  }
  if (path === '/api/auth/logout' && method === 'POST') return new Response(null, { status: 204 });
  if (path === '/api/health') return json({ db: 'ok', redis: 'ok', s3: 'ok' });

  const user = userOf(init);
  if (!user) return err(401, 'Not authenticated');
  const need = (...roles: Role[]) => (roles.includes(user.role) ? null : err(403, `Requires role ${roles.join(' or ')}`));

  if (path === '/api/me') return json(user);
  if (path === '/api/sessions' && method === 'GET') return json([...s.sessions.values()].map((d) => d.session));
  if (path === '/api/sessions/import' && method === 'POST') {
    const deny = need('OPERATOR', 'ADMIN');
    if (deny) return deny;
    const flight = String(body?.flight ?? '').trim();
    if (!/^[\w.-]+$/.test(flight)) return err(422, 'flight must be a flight folder name, e.g. 20261010_120643');
    if (s.sessions.has(flight)) return err(409, `Session ${flight} already imported`);
    const town = body?.scene && TOWNS.includes(body.scene) ? body.scene : 'Town05';
    const d = generateSession({ id: flight, name: `Flight ${flight} (mock import)`, source: 'carla', scene: await loadScene(town), seed: flight.length * 31 + s.sessions.size, startedAt: new Date().toISOString() });
    if (body?.profile) d.session.profile = body.profile;
    s.sessions.set(flight, d);
    return json(d.session, 201);
  }
  let m = /^\/api\/sessions\/([^/]+)(\/trajectories)?$/.exec(path);
  if (m) {
    const d = s.sessions.get(decodeURIComponent(m[1]));
    if (!d) return err(404, 'Session not found');
    return json(m[2] ? d.trajectories : d.session);
  }
  if (path === '/api/events' && method === 'GET') {
    const items = filtered(s, q);
    const limit = Number(q.get('limit') ?? 200);
    const offset = Number(q.get('offset') ?? 0);
    return json({ total: items.length, items: items.slice(offset, offset + limit).map(strip) });
  }
  m = /^\/api\/events\/([^/]+)(\/review)?$/.exec(path);
  if (m) {
    const ev = allEvents(s).find((e) => e.event_id === decodeURIComponent(m![1]));
    if (!ev) return err(404, 'Event not found');
    if (!m[2]) return json(strip(ev));
    const deny = isAnomaly(ev) ? need('OFFICER', 'ADMIN', 'MAINTENANCE') : need('OFFICER', 'ADMIN');
    if (deny) return deny;
    if (!['confirmed', 'dismissed'].includes(body?.outcome)) return err(422, 'outcome must be confirmed or dismissed');
    ev.review = { outcome: body.outcome, by: user.username, at: new Date().toISOString(), ...(body.note ? { note: body.note } : {}) };
    return json(strip(ev));
  }
  if (path === '/api/stats') return json(stats(filtered(s, q)));
  if (path === '/api/reports') return json({ total: 0, items: [] });  // the mock keeps no report history
  if (path === '/api/live/sources') return json([]);  // no live camera in the mock
  if (path === '/api/export') {
    const fmt = q.get('format') ?? 'csv';
    if (!['pdf', 'xlsx', 'geojson', 'csv'].includes(fmt)) return err(422, 'format must be pdf, xlsx, geojson or csv');
    const { body: b, type } = exportBody(fmt, filtered(s, q).map(strip));
    const ext = fmt === 'pdf' || fmt === 'xlsx' ? `${fmt}.txt` : fmt;
    return new Response(b, { headers: { 'Content-Type': type, 'Content-Disposition': `attachment; filename="events_mock.${ext}"` } });
  }
  if (path === '/api/profiles') return json([...s.profiles.values()].map((rows) => rows[rows.length - 1]));
  m = /^\/api\/profiles\/([^/]+)(\/history)?$/.exec(path);
  if (m) {
    const name = decodeURIComponent(m[1]);
    const rows = s.profiles.get(name);
    if (method === 'PUT') {
      const deny = need('ADMIN', 'PLANNER');
      if (deny) return deny;
      const p = body?.profile as Profile;
      const errors = validateProfile(p).filter((i) => i.level === 'error');
      if (errors.length) return err(422, errors.map((e) => `${e.path}: ${e.message}`).join('; '));
      if (p.name !== name) return err(422, 'profile.name must match the URL');
      const list = rows ?? [];
      const row = { version: list.length + 1, by: user.username, at: new Date().toISOString(), note: body?.note ?? '', profile: p };
      list.push(row);
      s.profiles.set(name, list);
      return json(row);
    }
    if (!rows) return err(404, 'Profile not found');
    return json(m[2] ? [...rows].reverse() : rows[rows.length - 1]);
  }
  if (path === '/api/conditions') return json(CONDITIONS);
  if (path === '/api/scenes') return json(TOWNS);
  m = /^\/api\/scenes\/([^/]+)$/.exec(path);
  if (m) return json(await loadScene(decodeURIComponent(m[1])));
  if (path === '/api/recommendations') return json(s.recommendations);
  m = /^\/api\/recommendations\/([^/]+)\/decision$/.exec(path);
  if (m && method === 'POST') {
    const deny = need('PLANNER', 'ADMIN');
    if (deny) return deny;
    const rec = s.recommendations.find((r) => r.id === decodeURIComponent(m![1]));
    if (!rec) return err(404, 'Recommendation not found');
    if (!['accepted', 'rejected', 'modified'].includes(body?.decision)) return err(422, 'invalid decision');
    if (!String(body?.rationale ?? '').trim()) return err(422, 'rationale is required');
    rec.status = body.decision;
    const entry = { id: s.history.length + 1, recommendation_id: rec.id, decision: body.decision, rationale: body.rationale, by: user.username, at: new Date().toISOString(), validation: null };
    s.history.unshift(entry);
    return json(entry);
  }
  if (path === '/api/planner/history') return json(s.history);
  return err(404, `Mock: no route ${method} ${path}`);
}

/** Mock live feed: replays the mock_live session in a loop at real speed, 10 Hz. */
export function mockLiveSocket(
  sessionId: string,
  onMessage: (msg: { type: 'vehicles'; items: VehicleState[] } | { type: 'event'; event: TrafficEvent }) => void,
  onStatus: (s: 'open' | 'closed') => void,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setInterval> | undefined;
  initState().then((s) => {
    if (stopped) return;
    const d = s.sessions.get(sessionId) ?? s.sessions.get('mock_live');
    if (!d) return;
    const tracks = Object.entries(d.trajectories);
    const tMin = Math.min(...tracks.map(([, p]) => p[0][0]));
    const tMax = Math.max(...tracks.map(([, p]) => p[p.length - 1][0]));
    const wall0 = performance.now();
    let lastT = tMin;
    onStatus('open');
    timer = setInterval(() => {
      const t = tMin + (((performance.now() - wall0) / 1000) % (tMax - tMin));
      if (t < lastT) lastT = tMin; // loop
      const items: VehicleState[] = [];
      for (const [tid, pts] of tracks) {
        if (t < pts[0][0] || t > pts[pts.length - 1][0]) continue;
        const i = Math.min(pts.length - 1, Math.max(1, Math.round((t - pts[0][0]) / 0.2)));
        const [, x, y, v] = pts[i];
        const [, px, py] = pts[i - 1];
        const evs = d.events.filter((e) => !isAnomaly(e) && e.track_ids.includes(Number(tid)));
        const flagged = evs.some((e) => eventTime(e) <= t);
        const checking = evs.some((e) => !isAnomaly(e) && e.start_s <= t && e.flag_s > t);
        items.push({ session_id: d.session.session_id, track_id: Number(tid), cls: 'car', x, y, speed_kmh: v, heading_deg: (Math.atan2(y - py, x - px) * 180) / Math.PI, lane_id: null, state: flagged ? 'flagged' : checking ? 'checking' : 'ok', t_s: t });
      }
      onMessage({ type: 'vehicles', items });
      for (const e of d.events) {
        const et = eventTime(e);
        if (et > lastT && et <= t) onMessage({ type: 'event', event: strip(e) });
      }
      lastT = t;
    }, 100);
  });
  return () => {
    stopped = true;
    if (timer) clearInterval(timer);
    onStatus('closed');
  };
}
