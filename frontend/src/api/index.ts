// App-wide API instance: real backend through the Vite proxy, or the in-browser mock when VITE_MOCK=1.

import { createApiClient, tokenExp, type FetchLike } from './client';

export { keepMediaUrl } from './client';
import type { LiveMessage } from './types';

export const MOCK = import.meta.env.VITE_MOCK === '1';

const TOKEN_KEY = 'aerial.token';
const REFRESH_KEY = 'aerial.refresh';
let unauthorizedHandler: (() => void) | null = null;
const listeners = new Set<(token: string | null) => void>();

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, v: string | null) {
  try {
    if (v) localStorage.setItem(key, v);
    else localStorage.removeItem(key);
  } catch {
    /* storage blocked: session-only login */
  }
}

/** Access token (short-lived JWT) + refresh token (single-use), shared by every tab via localStorage. */
export const tokenStore = {
  get: (): string | null => read(TOKEN_KEY),
  refresh: (): string | null => read(REFRESH_KEY),
  /** set(access, refresh) after login / refresh; set(null) clears both. */
  set(t: string | null, refresh?: string | null) {
    write(TOKEN_KEY, t);
    if (refresh !== undefined || !t) write(REFRESH_KEY, t ? refresh ?? null : null);
    scheduleRefresh();
    listeners.forEach((fn) => fn(t));
  },
  /** Called with the new access token whenever it changes. */
  subscribe(fn: (token: string | null) => void): () => void {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  },
};

export function onUnauthorized(fn: () => void) {
  unauthorizedHandler = fn;
}

// The mock module is loaded lazily so a real build does not run its data generation.
const mockFetch: FetchLike = async (input, init) => (await import('../mock/server')).mockFetch(input, init);

let refreshing: Promise<boolean> | null = null;

/** POST /api/auth/refresh with the stored refresh token (one call at a time); false = log in again. */
export function refreshTokens(): Promise<boolean> {
  refreshing ??= (async () => {
    const rt = tokenStore.refresh();
    if (!rt) return false;
    try {
      const r = await api.refresh(rt);
      tokenStore.set(r.access_token, r.refresh_token ?? null);
      return true;
    } catch {
      // another tab may have rotated it first: then the store already has the new pair
      return tokenStore.refresh() !== rt && !!tokenStore.get();
    }
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

// renew a minute before the access token expires, so media links, the WebSocket and the twin keep a valid one
let timer: ReturnType<typeof setTimeout> | undefined;
function scheduleRefresh() {
  if (timer) clearTimeout(timer);
  const exp = tokenExp(tokenStore.get());
  if (exp === null || !tokenStore.refresh()) return;
  timer = setTimeout(() => void refreshTokens(), Math.max(5000, exp * 1000 - Date.now() - 60000));
}

export const api = createApiClient({
  fetch: MOCK ? mockFetch : (input, init) => fetch(input, init),
  getToken: () => tokenStore.get(),
  refresh: refreshTokens,
  onUnauthorized: () => unauthorizedHandler?.(),
});
scheduleRefresh();

/** Evidence URLs from the API are signed (?exp=&sig=, expire after ~1 h) and need no token; other /api
 * URLs get the token as a query param (media tags cannot send headers). */
export function mediaUrl(url: string | undefined | null): string | null {
  if (!url) return null;
  if (url.startsWith('/api/') && !/[?&]sig=/.test(url)) {
    const t = tokenStore.get();
    return t ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(t)}` : url;
  }
  return url;
}

export type LiveStatus = 'connecting' | 'open' | 'closed' | 'error';

/**
 * Opens WS /api/ws/live with reconnect (1 s, 2 s, 4 s ... up to 10 s). Returns a close function.
 */
export function openLive(
  sessionId: string | null,
  onMessage: (m: LiveMessage) => void,
  onStatus: (s: LiveStatus) => void,
): () => void {
  if (MOCK) {
    let close: (() => void) | null = null;
    let cancelled = false;
    onStatus('connecting');
    import('../mock/server').then(({ mockLiveSocket }) => {
      if (!cancelled) close = mockLiveSocket(sessionId ?? 'mock_live', onMessage, (s) => onStatus(s));
    });
    return () => {
      cancelled = true;
      close?.();
    };
  }
  let ws: WebSocket | null = null;
  let retry = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let closed = false;

  const connect = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const p = new URLSearchParams();
    if (sessionId) p.set('session_id', sessionId);
    const t = tokenStore.get();
    if (t) p.set('token', t);
    onStatus('connecting');
    ws = new WebSocket(`${proto}://${location.host}/api/ws/live?${p}`);
    ws.onopen = () => {
      retry = 0;
      onStatus('open');
    };
    ws.onmessage = (ev) => {
      try {
        onMessage(JSON.parse(ev.data as string) as LiveMessage);
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onerror = () => onStatus('error');
    ws.onclose = () => {
      if (closed) return;
      onStatus('closed');
      timer = setTimeout(connect, Math.min(10000, 1000 * 2 ** retry++));
    };
  };
  connect();
  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    ws?.close();
  };
}
