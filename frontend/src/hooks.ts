// Shared queries: conditions (names for ids), sessions, and a session's lane map.

import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { api } from './api';
import { conditionIndex } from './lib/format';

export function useConditions() {
  const q = useQuery({ queryKey: ['conditions'], queryFn: api.conditions, staleTime: Infinity });
  const idx = useMemo(() => conditionIndex(q.data?.conditions), [q.data]);
  return { ...q, idx };
}

export function useSessions() {
  return useQuery({ queryKey: ['sessions'], queryFn: api.sessions });
}

/** Town of a session: its `town`, else the scene name, else null. */
export function sessionTown(s: { town?: string | null; scene?: string | null } | undefined): string | null {
  if (!s) return null;
  const t = s.town ?? s.scene ?? null;
  return t ? (t.split('/').pop() ?? t).replace(/\.json$/, '') : null;
}

export function useScene(town: string | null) {
  return useQuery({
    queryKey: ['scene', town],
    queryFn: () => api.scene(town!),
    enabled: !!town,
    staleTime: Infinity,
  });
}
