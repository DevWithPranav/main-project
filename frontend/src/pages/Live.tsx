// Live monitoring: vehicles on the lane map coloured by rule state, the overlay video, the 3D twin,
// the event feed and system health.
// "Live" listens to WS /api/ws/live; "Replay" plays an imported session from its trajectories,
// with events appearing at their flag time, so the page works without a live source.
// Replay keeps one clock (t, session seconds) for the map, the video and the twin: while the video
// plays it drives t; a slider seek moves the video; the twin iframe gets t by postMessage.

import { ActionIcon, Alert, Badge, Button, Card, Grid, Group, Loader, Progress, ScrollArea, SegmentedControl, Select, Slider, Stack, Tabs, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconCube, IconExternalLink, IconMap, IconPlayerPause, IconPlayerPlay, IconPlayerSkipBack } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, openLive, tokenStore, TWIN_URL, type LiveStatus } from '../api';
import { eventTime, isAnomaly, type SessionVideo, type TrafficEvent, type VehicleState } from '../api/types';
import { useAuth } from '../auth';
import EventDrawer from '../components/EventDrawer';
import LaneMap, { type MapPoint, type Trail } from '../components/LaneMap';
import { sessionTown, useScene, useSessions } from '../hooks';
import { eventSummary, fmtSimTime, TYPE_COLORS, typeLabel } from '../lib/format';
import { can } from '../lib/permissions';
import { sessionToVideo, trailOf, videoToSession } from '../lib/videoSync';

const STATE_COLORS = { ok: '#40c057', checking: '#fab005', flagged: '#fa5252' } as const;
const MANTINE_HEX: Record<string, string> = {
  red: '#fa5252', orange: '#fd7e14', yellow: '#fab005', grape: '#be4bdb', violet: '#7950f2', blue: '#228be6',
  cyan: '#15aabf', teal: '#12b886', green: '#40c057', pink: '#e64980', indigo: '#4c6ef5', gray: '#868e96', lime: '#82c91e',
};
const hex = (c: string | undefined) => MANTINE_HEX[c ?? 'gray'] ?? c ?? '#868e96';
const TRAIL_S = 3; // trail length, seconds (same as the twin)
const TWIN_ORIGIN = (() => {
  try {
    return new URL(TWIN_URL).origin;
  } catch {
    return '*';
  }
})();

function Health() {
  const q = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 10_000 });
  if (q.isError) return <Badge color="red">backend unreachable</Badge>;
  return (
    <Group gap={4}>
      {Object.entries(q.data ?? {}).map(([k, v]) =>
        typeof v === 'string' ? (
          <Badge key={k} color={v === 'ok' ? 'green' : 'red'} variant="light" title={v}>{k}</Badge>
        ) : null,
      )}
      {/* evidence clips still being made browser-playable in the background (backend media.py) */}
      {q.data?.clips && (q.data.clips.pending > 0 || q.data.clips.failed > 0) && (
        <Badge color={q.data.clips.failed ? 'orange' : 'blue'} variant="light"
          title={`${q.data.clips.done} done, ${q.data.clips.pending} pending, ${q.data.clips.failed} failed`}>
          clips {q.data.clips.pending} pending
        </Badge>
      )}
    </Group>
  );
}

/** Position of a track at time t (linear between samples), with heading; null outside its span. */
function at(samples: [number, number, number, number][], t: number) {
  if (!samples.length || t < samples[0][0] || t > samples[samples.length - 1][0]) return null;
  let lo = 0, hi = samples.length - 1;
  while (hi - lo > 1) {
    const m = (lo + hi) >> 1;
    if (samples[m][0] <= t) lo = m; else hi = m;
  }
  const a = samples[lo], b = samples[hi];
  const k = b[0] > a[0] ? (t - a[0]) / (b[0] - a[0]) : 0;
  return {
    x: a[1] + (b[1] - a[1]) * k,
    y: a[2] + (b[2] - a[2]) * k,
    v: a[3] + (b[3] - a[3]) * k,
    h: (Math.atan2(b[2] - a[2], b[1] - a[1]) * 180) / Math.PI,
  };
}

/** The 3D twin in an iframe: logged in with this page's token, kept on the same session and time. */
function TwinFrame({ sid, t, mode, town }: { sid: string | null; t: number; mode: 'replay' | 'live'; town: string | null }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const { me } = useAuth();
  const [ready, setReady] = useState(false);
  const post = (msg: unknown) => ref.current?.contentWindow?.postMessage(msg, TWIN_ORIGIN);
  useEffect(() => {
    const onMsg = (m: MessageEvent) => {
      if (m.source !== ref.current?.contentWindow || (TWIN_ORIGIN !== '*' && m.origin !== TWIN_ORIGIN)) return;
      if ((m.data as { type?: string })?.type === 'twin-ready') {
        post({ type: 'auth', token: tokenStore.get(), username: me?.username, role: me?.role, session: sid, mode, t });
        setReady(true);
      }
    };
    window.addEventListener('message', onMsg);
    return () => window.removeEventListener('message', onMsg);
  }, [me, sid, mode, t]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (ready && sid) post({ type: 'session', id: sid }); }, [ready, sid]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (ready) post({ type: 'mode', mode }); }, [ready, mode]); // eslint-disable-line react-hooks/exhaustive-deps
  // seek at <= 10 Hz: the twin redraws every vehicle on each message
  const lastSent = useRef(0);
  useEffect(() => {
    if (!ready || mode !== 'replay') return;
    const now = performance.now();
    if (now - lastSent.current < 100) return;
    lastSent.current = now;
    post({ type: 'seek', t });
  }, [ready, t, mode]); // eslint-disable-line react-hooks/exhaustive-deps
  const src = `${TWIN_URL}/?embed=1${sid ? `&session=${encodeURIComponent(sid)}` : ''}${town ? `&town=${encodeURIComponent(town)}` : ''}`;
  return <iframe ref={ref} src={src} title="3D digital twin" style={{ width: '100%', height: '62vh', border: 0, borderRadius: 8, background: '#141517' }} />;
}

function VideoPanel({ sid, info, videoRef, onRequest, canMake }: {
  sid: string | null; info: SessionVideo | undefined; videoRef: React.RefObject<HTMLVideoElement | null>;
  onRequest: () => void; canMake: boolean;
}) {
  if (!sid) return null;
  if (!info) return <Loader size="sm" />;
  if (info.status === 'ready' && info.url)
    return <video ref={videoRef} src={info.url} muted playsInline preload="auto" style={{ width: '100%', borderRadius: 8, background: '#000', display: 'block' }} />;
  if (info.status === 'encoding')
    return (
      <Stack gap={4}>
        <Text size="sm">Preparing the overlay video for the browser…</Text>
        <Progress value={(info.progress ?? 0) * 100} animated />
      </Stack>
    );
  return (
    <Stack gap={6}>
      {info.status === 'failed' && <Alert color="red" p="xs">Video failed: {info.error}</Alert>}
      <Text size="sm" c="dimmed">No browser video for this session yet (the pipeline writes mp4v, which browsers can't play).</Text>
      {canMake && <Button size="xs" variant="light" onClick={onRequest}>Prepare video (WebM, takes a few minutes)</Button>}
    </Stack>
  );
}

export default function LivePage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const sessions = useSessions();
  const [sp, setSp] = useSearchParams();
  const sid = sp.get('session');
  const setSid = (v: string | null) => setSp(v ? { session: v } : {}, { replace: true });
  const setTown = (v: string | null) => setSp({ ...(sid ? { session: sid } : {}), ...(v ? { town: v } : {}) }, { replace: true });
  const [mode, setMode] = useState<'replay' | 'live'>('replay');
  const [view, setView] = useState<string | null>('map');
  const [selected, setSelected] = useState<TrafficEvent | null>(null);
  useEffect(() => {
    if (!sid && sessions.data?.length) setSid(sessions.data[0].session_id);
  }, [sessions.data, sid]); // eslint-disable-line react-hooks/exhaustive-deps
  const session = sessions.data?.find((s) => s.session_id === sid);
  // a live session carries no town (POST /api/events creates it bare): pick the map here
  const scenes = useQuery({ queryKey: ['scenes'], queryFn: api.scenes, staleTime: Infinity });
  const townNames = (scenes.data ?? []).map((x) => (typeof x === 'string' ? x : String(x.town ?? x.scene ?? x.name ?? ''))).filter(Boolean);
  const ownTown = sessionTown(session);
  const fallbackTown = sp.get('town') ?? sessionTown(sessions.data?.find((x) => sessionTown(x))) ?? townNames[0] ?? null;
  // a real clip (source video) is in its own site's metres: its map comes with the session, not a town
  const isVideo = session?.source === 'video';
  const town = isVideo ? null : ownTown ?? fallbackTown;
  const townScene = useScene(town);
  const siteScene = useQuery({ queryKey: ['sessionScene', sid], queryFn: () => api.sessionScene(sid!), enabled: !!sid && isVideo, staleTime: Infinity });
  const scene = isVideo ? siteScene : townScene;

  // ---- replay
  const traj = useQuery({ queryKey: ['traj', sid], queryFn: () => api.trajectories(sid!), enabled: !!sid && mode === 'replay' });
  const evq = useQuery({ queryKey: ['events', { session_id: sid, all: true }], queryFn: () => api.events({ session_id: sid! }, { limit: 2000 }), enabled: !!sid });
  // flight times are simulator seconds (a flight may start at t = 460 s), so replay spans [tMin, tMax]
  const [tMin, tMax] = useMemo(() => {
    let lo = Infinity, hi = 0;
    Object.values(traj.data ?? {}).forEach((s) => {
      if (s.length) { lo = Math.min(lo, s[0][0]); hi = Math.max(hi, s[s.length - 1][0]); }
    });
    return [isFinite(lo) ? lo : 0, hi];
  }, [traj.data]);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState('2');

  // ---- video (replay)
  const video = useQuery({
    queryKey: ['video', sid],
    queryFn: () => api.sessionVideo(sid!),
    enabled: !!sid && mode === 'replay',
    refetchInterval: (q) => (q.state.data?.status === 'encoding' ? 5000 : false),
  });
  const makeVideo = useMutation({
    mutationFn: () => api.makeSessionVideo(sid!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['video', sid] }),
    onError: (e: Error) => notifications.show({ color: 'red', title: 'Video', message: e.message }),
  });
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const vinfo = video.data?.status === 'ready' && video.data.frame_t?.length ? video.data : null;
  const frameT = vinfo?.frame_t ?? [];
  const fps = vinfo?.fps ?? 1;

  // the clock: the video while it plays, else wall time x speed
  const last = useRef<number | null>(null);
  useEffect(() => {
    if (!playing) return;
    const v = videoRef.current;
    if (vinfo && v) {
      v.playbackRate = Number(speed);
      v.currentTime = sessionToVideo(t, frameT, fps);
      v.play().catch(() => undefined);
    }
    let raf = 0;
    const step = (now: number) => {
      const vv = videoRef.current;
      if (vinfo && vv && !vv.paused && !vv.ended) setT(videoToSession(vv.currentTime, frameT, fps));
      else if (last.current != null) setT((x) => Math.min(tMax, x + ((now - last.current!) / 1000) * Number(speed)));
      last.current = now;
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => { cancelAnimationFrame(raf); last.current = null; videoRef.current?.pause(); };
  }, [playing, speed, tMax, vinfo]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (t >= tMax && playing) setPlaying(false); }, [t, tMax, playing]);
  useEffect(() => { setT(tMin); setPlaying(false); }, [sid, tMin]);
  // paused: a seek on the slider moves the video to the same moment
  useEffect(() => {
    const v = videoRef.current;
    if (!vinfo || !v || playing) return;
    const want = sessionToVideo(t, frameT, fps);
    if (Math.abs(v.currentTime - want) > 0.5 / fps) v.currentTime = want;
  }, [t, playing, vinfo]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- live
  const [liveVehicles, setLiveVehicles] = useState<VehicleState[]>([]);
  const [liveEvents, setLiveEvents] = useState<TrafficEvent[]>([]);
  const [status, setStatus] = useState<LiveStatus>('closed');
  const liveTrails = useRef(new Map<number, [number, number, number][]>()); // track -> [t_s, x, y]
  useEffect(() => {
    if (mode !== 'live') return;
    setLiveVehicles([]); setLiveEvents([]); liveTrails.current.clear();
    return openLive(sid, (m) => {
      if (m.type === 'vehicles') {
        const seen = new Set<number>();
        for (const v of m.items) {
          seen.add(v.track_id);
          const tr = liveTrails.current.get(v.track_id) ?? [];
          if (!tr.length || tr[tr.length - 1][0] !== v.t_s) tr.push([v.t_s, v.x, v.y]);
          while (tr.length && tr[0][0] < v.t_s - TRAIL_S) tr.shift();
          liveTrails.current.set(v.track_id, tr);
        }
        for (const k of [...liveTrails.current.keys()]) if (!seen.has(k)) liveTrails.current.delete(k);
        setLiveVehicles(m.items);
      } else {
        setLiveEvents((l) => [m.event, ...l.filter((e) => e.event_id !== m.event.event_id)].slice(0, 200));
        qc.invalidateQueries({ queryKey: ['events'] });
        notifications.show({ color: isAnomaly(m.event) ? 'lime' : 'red', title: typeLabel(m.event.type), message: eventSummary(m.event), autoClose: 4000 });
      }
    }, setStatus);
  }, [mode, sid]); // eslint-disable-line react-hooks/exhaustive-deps

  const events = evq.data?.items ?? [];
  const shownEvents = useMemo(() => {
    if (mode === 'live') {
      // events stored for this session (page reload) plus those pushed since
      const m = new Map(events.map((e) => [e.event_id, e]));
      liveEvents.forEach((e) => m.set(e.event_id, e));
      return [...m.values()].sort((a, b) => eventTime(b) - eventTime(a));
    }
    return events.filter((e) => eventTime(e) <= t).sort((a, b) => eventTime(b) - eventTime(a));
  }, [mode, events, liveEvents, t]);

  const { vehicles, trails } = useMemo(() => {
    const vs: MapPoint[] = [];
    const ts: Trail[] = [];
    if (mode === 'live') {
      for (const v of liveVehicles) {
        vs.push({ id: v.track_id, x: v.x, y: v.y, heading_deg: v.heading_deg, color: STATE_COLORS[v.state] ?? STATE_COLORS.ok, label: `${Math.round(v.speed_kmh)}` });
        const tr = liveTrails.current.get(v.track_id);
        if (tr) ts.push({ id: v.track_id, points: tr.map((p) => [p[1], p[2]]), color: STATE_COLORS[v.state] ?? STATE_COLORS.ok });
      }
      return { vehicles: vs, trails: ts };
    }
    for (const [tid, s] of Object.entries(traj.data ?? {})) {
      const p = at(s, t);
      if (!p) continue;
      let state: keyof typeof STATE_COLORS = 'ok';
      for (const e of events) {
        if (isAnomaly(e) || !e.track_ids.includes(Number(tid))) continue;
        if (e.flag_s <= t && t <= (e.end_s ?? e.flag_s + 8)) state = 'flagged';
        else if (state === 'ok' && e.start_s <= t && t < e.flag_s) state = 'checking';
      }
      vs.push({ id: tid, x: p.x, y: p.y, heading_deg: p.h, color: STATE_COLORS[state], label: `${Math.round(p.v)}` });
      ts.push({ id: tid, points: [...trailOf(s, t, TRAIL_S), [p.x, p.y]], color: STATE_COLORS[state] });
    }
    return { vehicles: vs, trails: ts };
  }, [mode, liveVehicles, traj.data, t, events]);

  const pins: MapPoint[] = shownEvents.slice(0, 200).map((e) => ({
    id: e.event_id, x: e.x, y: e.y, color: hex(TYPE_COLORS[e.type]), label: eventSummary(e), shape: isAnomaly(e) ? 'diamond' : 'circle',
  }));
  const twinLink = `${TWIN_URL}/?${new URLSearchParams({ ...(sid ? { session: sid } : {}), ...(mode === 'replay' ? { t: t.toFixed(1) } : { mode: 'live' }) })}`;

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Live monitoring</Title>
        <Health />
      </Group>
      <Group>
        <Select
          placeholder={sessions.isLoading ? 'Loading sessions…' : 'Pick a session'}
          data={(sessions.data ?? []).map((s) => ({ value: s.session_id, label: `${s.name || s.session_id} (${s.n_events} events)` }))}
          value={sid}
          onChange={setSid}
          w={340}
          searchable
        />
        {session && !ownTown && (isVideo
          ? <Badge variant="outline" color="gray">site map</Badge>
          : <Select w={130} data={townNames} value={town} onChange={setTown} aria-label="Town" placeholder="Town" />)}
        <SegmentedControl value={mode} onChange={(v) => { setPlaying(false); setMode(v as 'replay' | 'live'); }} data={[{ value: 'replay', label: 'Replay' }, { value: 'live', label: 'Live' }]} />
        {mode === 'live' && <Badge color={status === 'open' ? 'green' : status === 'connecting' ? 'yellow' : 'red'}>{status}</Badge>}
        {mode === 'live' && status === 'open' && <Text size="xs" c="dimmed">{liveVehicles.length} vehicles</Text>}
        <Group gap={6}>
          {Object.entries(STATE_COLORS).map(([k, c]) => (
            <Badge key={k} variant="dot" color={c}>{k}</Badge>
          ))}
        </Group>
        <Button component="a" href={twinLink} target="_blank" size="xs" variant="subtle" leftSection={<IconExternalLink size={14} />}>Open twin at this moment</Button>
      </Group>
      <Grid>
        <Grid.Col span={{ base: 12, md: 8 }}>
          <Card withBorder p="xs">
            <Tabs value={view} onChange={setView} keepMounted={false}>
              <Tabs.List mb="xs">
                <Tabs.Tab value="map" leftSection={<IconMap size={14} />}>Map</Tabs.Tab>
                <Tabs.Tab value="twin" leftSection={<IconCube size={14} />}>3D twin</Tabs.Tab>
              </Tabs.List>
              <Tabs.Panel value="map">
                {scene.isLoading ? <Loader m="xl" /> : (
                  <LaneMap lanes={scene.data?.lanes} zones={scene.data?.zones} vehicles={vehicles} trails={trails} pins={pins}
                    onPinClick={(id) => setSelected(shownEvents.find((e) => e.event_id === id) ?? null)} height="62vh" />
                )}
              </Tabs.Panel>
              <Tabs.Panel value="twin">
                <TwinFrame sid={sid} t={t} mode={mode} town={town} />
              </Tabs.Panel>
            </Tabs>
            {mode === 'replay' && (
              <Group mt="xs" wrap="nowrap">
                <ActionIcon variant="light" onClick={() => setT(tMin)} aria-label="Back to start"><IconPlayerSkipBack size={16} /></ActionIcon>
                <ActionIcon variant="filled" onClick={() => setPlaying((p) => !p)} disabled={!tMax} aria-label={playing ? 'Pause' : 'Play'}>
                  {playing ? <IconPlayerPause size={16} /> : <IconPlayerPlay size={16} />}
                </ActionIcon>
                <Slider style={{ flex: 1 }} min={tMin} max={Math.max(tMax, tMin + 1)} step={0.1} value={t} onChange={(v) => { setPlaying(false); setT(v); }} label={fmtSimTime} />
                <Text size="sm" w={110} ta="right">{fmtSimTime(t)} / {fmtSimTime(tMax)}</Text>
                <Select w={80} data={['1', '2', '4', '8']} value={speed} onChange={(v) => setSpeed(v ?? '1')} aria-label="Speed" />
              </Group>
            )}
            {mode === 'replay' && traj.isFetching && <Text size="xs" c="dimmed">loading trajectories…</Text>}
          </Card>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 4 }}>
          <Stack gap="sm" h="100%">
            <Card withBorder p="xs">
              <Group justify="space-between" mb={6}>
                <Text fw={600}>Video {mode === 'replay' && vinfo ? <Text span size="xs" c="dimmed">({vinfo.source}, synced)</Text> : null}</Text>
              </Group>
              {mode === 'replay' ? (
                <VideoPanel sid={sid} info={video.data} videoRef={videoRef} onRequest={() => makeVideo.mutate()} canMake={can(me?.role, 'import_session')} />
              ) : (
                <Text size="sm" c="dimmed">The live pipeline sends vehicle states and events; it does not stream video to the dashboard yet.</Text>
              )}
            </Card>
            <Card withBorder p="xs" style={{ flex: 1 }}>
              <Text fw={600} mb="xs">Events ({shownEvents.length})</Text>
              <ScrollArea h={mode === 'replay' && vinfo ? '32vh' : '46vh'}>
                <Stack gap={6}>
                  {shownEvents.length === 0 && <Text size="sm" c="dimmed">{mode === 'live' ? 'Waiting for events…' : 'No events yet at this time.'}</Text>}
                  {shownEvents.map((e) => (
                    <Card key={e.event_id} withBorder p={8} style={{ cursor: 'pointer' }} onClick={() => setSelected(e)}>
                      <Group justify="space-between" wrap="nowrap">
                        <Group gap={4}>
                          <Badge color={TYPE_COLORS[e.type] ?? 'gray'} size="sm">{typeLabel(e.type)}</Badge>
                          {isAnomaly(e) && <Badge size="xs" variant="outline" color="lime">road surface</Badge>}
                        </Group>
                        <Text size="xs" c="dimmed">{fmtSimTime(eventTime(e))}</Text>
                      </Group>
                      <Text size="xs" mt={4}>{eventSummary(e)}</Text>
                    </Card>
                  ))}
                </Stack>
              </ScrollArea>
            </Card>
          </Stack>
        </Grid.Col>
      </Grid>
      <EventDrawer event={selected} onClose={() => setSelected(null)} />
    </Stack>
  );
}
