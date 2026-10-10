// Login state: the access + refresh tokens live in tokenStore; `me` comes from /api/me. The client
// refreshes an expired access token; a 401 that refresh cannot fix logs out.

import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, onUnauthorized, tokenStore } from './api';
import type { Me } from './api/types';

interface AuthCtx {
  me: Me | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(!!tokenStore.get());

  // local sign-out (a 401 the refresh token could not fix)
  const clear = useCallback(() => {
    tokenStore.set(null);
    setMe(null);
    qc.clear();
  }, [qc]);

  // the logout button: revoke both tokens on the server (best effort), then sign out here
  const logout = useCallback(() => {
    const rt = tokenStore.refresh();
    if (tokenStore.get() || rt) void api.logout(rt).catch(() => undefined).finally(clear);
    else clear();
  }, [clear]);

  useEffect(() => {
    onUnauthorized(clear);
    if (!tokenStore.get()) return;
    api
      .me()
      .then(setMe)
      .catch(() => tokenStore.set(null))
      .finally(() => setLoading(false));
  }, [clear]);

  const login = useCallback(async (username: string, password: string) => {
    const r = await api.login(username, password);
    tokenStore.set(r.access_token, r.refresh_token ?? null);
    setMe(await api.me());
  }, []);

  return <Ctx.Provider value={{ me, loading, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useAuth outside AuthProvider');
  return c;
}
