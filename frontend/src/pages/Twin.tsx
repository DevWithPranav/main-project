// 3D digital twin page (/twin): mounts a TwinView (Cesium) in a div and disposes it on unmount. The
// dashboard's login is used (no second login). URL: ?session=<id>&t=<sim s>&town=<Town03>
// &anchor=lat,lon[,h]&mode=live. Changing ?session= or ?t= while the page is open moves the twin;
// picking a session in the twin's panel writes ?session= back.

import { useComputedColorScheme } from '@mantine/core';
import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAuth } from '../auth';
import { TwinView } from '../twin/TwinView';

export default function TwinPage() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<TwinView | null>(null);
  const shown = useRef<string | null>(null); // the session the twin shows
  const dark = useComputedColorScheme('light') === 'dark';
  const session = params.get('session');
  const t = params.get('t');

  useEffect(() => {
    if (!host.current || !me) return;
    const num = (s: string | null) => (s !== null && s !== '' && Number.isFinite(Number(s)) ? Number(s) : null);
    shown.current = params.get('session');
    const v = new TwinView(host.current, {
      username: me.username,
      role: me.role,
      session: params.get('session'),
      t: num(params.get('t')),
      town: params.get('town'),
      anchor: params.get('anchor'),
      mode: params.get('mode') === 'live' ? 'live' : 'replay',
      dark,
      onSession: (id) => {
        shown.current = id;
        setParams((p) => {
          const n = new URLSearchParams(p);
          n.set('session', id);
          n.delete('t');
          return n;
        }, { replace: true });
      },
    });
    view.current = v;
    return () => {
      v.dispose();
      view.current = null;
    };
    // mounted once per user; URL changes are followed by the effects below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.username, me?.role]);

  useEffect(() => {
    if (session && view.current && session !== shown.current) {
      shown.current = session;
      void view.current.loadSession(session, t !== null && t !== '' && Number.isFinite(Number(t)) ? Number(t) : null);
    }
  }, [session, t]);

  useEffect(() => {
    if (t !== null && t !== '' && Number.isFinite(Number(t))) view.current?.seek(Number(t));
  }, [t]);

  useEffect(() => {
    view.current?.setDark(dark);
  }, [dark]);

  return (
    <div
      ref={host}
      style={{
        height: 'calc(100dvh - var(--app-shell-header-height, 56px) - 2 * var(--mantine-spacing-md))',
        minHeight: 420,
      }}
    />
  );
}
