// Login state: the JWT lives in tokenStore; `me` comes from /api/me. A 401 anywhere logs out.

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

  const logout = useCallback(() => {
    tokenStore.set(null);
    setMe(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    onUnauthorized(logout);
    if (!tokenStore.get()) return;
    api
      .me()
      .then(setMe)
      .catch(() => tokenStore.set(null))
      .finally(() => setLoading(false));
  }, [logout]);

  const login = useCallback(async (username: string, password: string) => {
    const r = await api.login(username, password);
    tokenStore.set(r.access_token);
    setMe(await api.me());
  }, []);

  return <Ctx.Provider value={{ me, loading, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useAuth outside AuthProvider');
  return c;
}
