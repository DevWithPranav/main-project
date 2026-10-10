// App-wide API instance: real backend through the Vite proxy, or the in-browser mock when VITE_MOCK=1.

import { createApiClient, type FetchLike } from './client';
import type { LiveMessage } from './types';

export const MOCK = import.meta.env.VITE_MOCK === '1';
export const TWIN_URL = import.meta.env.VITE_TWIN_URL ?? 'http://localhost:5174';

const TOKEN_KEY = 'aerial.token';
let unauthorizedHandler: (() => void) | null = null;

export const tokenStore = {
  get(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  set(t: string | null) {
    try {
      if (t) localStorage.setItem(TOKEN_KEY, t);
      else localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* storage blocked: session-only login */
    }
  },
};

export function onUnauthorized(fn: () => void) {
  unauthorizedHandler = fn;
}

// The mock module is loaded lazily so a real build does not run its data generation.
const mockFetch: FetchLike = async (input, init) => (await import('../mock/server')).mockFetch(input, init);

export const api = createApiClient({
  fetch: MOCK ? mockFetch : (input, init) => fetch(input, init),
  getToken: () => tokenStore.get(),
  onUnauthorized: () => unauthorizedHandler?.(),
});

/** Evidence URLs under /api need the token as a query param (media tags cannot send headers). */
export function mediaUrl(url: string | undefined | null): string | null {
  if (!url) return null;
  if (url.startsWith('/api/')) {
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
