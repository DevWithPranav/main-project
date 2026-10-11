// Live monitoring: the camera footage next to the model's output, vehicles on the lane map coloured
// by rule state, the event feed and system health. (The 3D twin has its own page.)
// "Live" listens to WS /api/ws/live and shows the pipeline's newest frames (MJPEG, /api/live/video);
// "Replay" plays an imported session from its trajectories, with events appearing at their flag
// time, so the page works without a live source.
// Replay keeps one clock (t, session seconds) for the map and both videos: while the model-output
// video (else the raw one) plays it drives t and the other follows; a slider seek moves both.

import { ActionIcon, Alert, Badge, Button, Card, Grid, Group, Loader, Progress, ScrollArea, SegmentedControl, Select, Slider, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconMap, IconPlayerPause, IconPlayerPlay, IconPlayerSkipBack } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api, keepMediaUrl, openLive, tokenStore, type LiveStatus } from '../api';
import { eventTime, isAnomaly, type SessionVideo, type TrafficEvent, type VehicleState, type VideoKind } from '../api/types';
import { useAuth } from '../auth';
import EventDrawer from '../components/EventDrawer';
import LaneMap, { type MapPoint, type Trail } from '../components/LaneMap';
import { sessionTown, useScene, useSessions } from '../hooks';
import { eventSummary, fmtSimTime, TYPE_COLORS, typeLabel } from '../lib/format';
import { can } from '../lib/permissions';
import { sessionToVideo, trailOf, videoToSession } from '../lib/videoSync';
import { featureToZone, zoneColor } from '../lib/zones';

const STATE_COLORS = { ok: '#40c057', checking: '#fab005', flagged: '#fa5252' } as const;
const MANTINE_HEX: Record<string, string> = {
  red: '#fa5252', orange: '#fd7e14', yellow: '#fab005', grape: '#be4bdb', violet: '#7950f2', blue: '#228be6',
  cyan: '#15aabf', teal: '#12b886', green: '#40c057', pink: '#e64980', indigo: '#4c6ef5', gray: '#868e96', lime: '#82c91e',
};
const hex = (c: string | undefined) => MANTINE_HEX[c ?? 'gray'] ?? c ?? '#868e96';
const TRAIL_S = 3; // trail length, seconds (same as the twin)

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

const KIND_TITLE: Record<VideoKind, string> = { raw: 'Camera footage (model input)', annotated: 'Model output' };
const FRAME_BOX = { aspectRatio: '16 / 9', width: '100%', borderRadius: 8, background: '#000', display: 'block', objectFit: 'contain' } as const;

function Placeholder({ children }: { children: React.ReactNode }) {
  return <Stack justify="center" align="center" gap={6} style={{ ...FRAME_BOX, padding: 12 }}>{children}</Stack>;
}

/** Replay: a session video (raw or annotated) as WebM, driven by the page clock. */
function VideoPanel({ kind, sid, info, videoRef, onRequest, canMake }: {
  kind: VideoKind; sid: string | null; info: SessionVideo | undefined; videoRef: React.RefObject<HTMLVideoElement | null>;
  onRequest: () => void; canMake: boolean;
}) {
  // a refetch re-signs the URL; keep the current one while it is valid so the <video> does not reload
  const src = useRef<string | null>(null);
  src.current = keepMediaUrl(src.current, info?.url ?? null);
  if (!sid) return <Placeholder><Text size="sm" c="dimmed">Pick a session</Text></Placeholder>;
  if (!info) return <Placeholder><Loader size="sm" /></Placeholder>;
  if (info.status === 'ready' && info.url)
    return <video ref={videoRef} src={src.current ?? info.url} muted playsInline preload="auto" style={FRAME_BOX} />;
  if (info.status === 'encoding')
    return (
      <Placeholder>
        <Text size="sm" c="gray.4">Preparing the {kind === 'raw' ? 'camera' : 'model output'} video… {Math.round((info.progress ?? 0) * 100)}%</Text>
        <Progress w="70%" value={(info.progress ?? 0) * 100} animated />
      </Placeholder>
    );
  return (
    <Placeholder>
      {info.status === 'failed' && <Alert color="red" p="xs">Video failed: {info.error}</Alert>}
      {info.available === false ? (
        <Text size="sm" c="gray.5" ta="center">{kind === 'raw' ? 'The original footage of this session is not on this machine.' : 'This session has no model output video.'}</Text>
      ) : (
        <>
          <Text size="sm" c="gray.5" ta="center">Not prepared for the browser yet.</Text>
          {canMake && <Button size="xs" variant="light" onClick={onRequest}>Prepare video (a few minutes)</Button>}
        </>
      )}
    </Placeholder>
  );
}

/** Live: the pipeline's newest frames as an MJPEG stream (GET /api/live/video). */
function LiveFeed({ kind, sid }: { kind: VideoKind; sid: string | null }) {
  const [state, setState] = useState<'waiting' | 'playing'>('waiting');
  const [attempt, setAttempt] = useState(0);
  // the token is read when the stream opens; a refresh later must not reload the <img>
  const src = useMemo(() => {
    if (!sid) return null;
    const p = new URLSearchParams({ session_id: sid, kind });
    const t = tokenStore.get();
    if (t) p.set('token', t);
    return `/api/live/video?${p}`;
  }, [sid, kind, attempt]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setState('waiting'), [src]);
  if (!src) return <Placeholder><Text size="sm" c="dimmed">Pick a session</Text></Placeholder>;
  return (
    <div style={{ position: 'relative' }}>
      <img key={src} src={src} alt={KIND_TITLE[kind]} style={FRAME_BOX} onLoad={() => setState('playing')}
        onError={() => { setState('waiting'); setTimeout(() => setAttempt((a) => a + 1), 3000); }} />
      {state === 'waiting' && (
        <Stack justify="center" align="center" gap={4} style={{ position: 'absolute', inset: 0 }}>
          <Loader size="sm" color="gray" />
          <Text size="sm" c="gray.5">Waiting for frames from the live pipeline…</Text>
        </Stack>
      )}
    </div>
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
  // zones drawn in Configuration > Zones for this town / site, on top of the map file's own (dashed)
  const zoneScene = isVideo ? sid : town;
  const apiZones = useQuery({ queryKey: ['zones', zoneScene, 'api-only'], queryFn: () => api.zones(zoneScene!), enabled: !!zoneScene, retry: false });
  const mapZones = useMemo(() => {
    const own = scene.data?.zones ?? [];
    const drawn = (apiZones.data ?? []).map(featureToZone).filter((z) => !own.some((o) => o.id === z.id));
    return [...own, ...drawn];
  }, [scene.data, apiZones.data]);

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

  // ---- videos (replay): the raw footage and the model output, both on the page clock
  const videoQuery = (kind: VideoKind) => ({
    queryKey: ['video', sid, kind],
    queryFn: () => api.sessionVideo(sid!, kind),
    enabled: !!sid && mode === 'replay',
    refetchInterval: (q: { state: { data?: SessionVideo } }) => (q.state.data?.status === 'encoding' ? 5000 : false as const),
  });
  const rawVideo = useQuery(videoQuery('raw'));
  const annVideo = useQuery(videoQuery('annotated'));
  const makeVideo = useMutation({
    mutationFn: (kind: VideoKind) => api.makeSessionVideo(sid!, kind),
    onSuccess: (_, kind) => qc.invalidateQueries({ queryKey: ['video', sid, kind] }),
    onError: (e: Error) => notifications.show({ color: 'red', title: 'Video', message: e.message }),
  });
  const rawRef = useRef<HTMLVideoElement | null>(null);
  const annRef = useRef<HTMLVideoElement | null>(null);
  const readyInfo = (d: SessionVideo | undefined) => (d?.status === 'ready' && d.frame_t?.length ? d : null);
  const players = useMemo(() => {
    const ps: { ref: React.RefObject<HTMLVideoElement | null>; info: SessionVideo }[] = [];
    const a = readyInfo(annVideo.data), r = readyInfo(rawVideo.data);
    if (a) ps.push({ ref: annRef, info: a });
    if (r) ps.push({ ref: rawRef, info: r });
    return ps; // the first one drives the clock
  }, [annVideo.data, rawVideo.data]);
  const master = players[0] ?? null;
  const vinfo = master?.info ?? null;
  const frameT = vinfo?.frame_t ?? [];
  const fps = vinfo?.fps ?? 1;

  // the clock: the leading video while it plays, else wall time x speed
  const last = useRef<number | null>(null);
  useEffect(() => {
    if (!playing) return;
    const v = master?.ref.current;
    if (vinfo && v) {
      v.playbackRate = Number(speed);
      v.currentTime = sessionToVideo(t, frameT, fps);
      v.play().catch(() => undefined);
    }
    let raf = 0;
    const step = (now: number) => {
      const vv = master?.ref.current;
      if (vinfo && vv && !vv.paused && !vv.ended) setT(videoToSession(vv.currentTime, frameT, fps));
      else if (last.current != null) setT((x) => Math.min(tMax, x + ((now - last.current!) / 1000) * Number(speed)));
      last.current = now;
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => { cancelAnimationFrame(raf); last.current = null; master?.ref.current?.pause(); };
  }, [playing, speed, tMax, master]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (t >= tMax && playing) setPlaying(false); }, [t, tMax, playing]);
  useEffect(() => { setT(tMin); setPlaying(false); }, [sid, tMin]);
  // paused: every video shows the clock's moment; playing: the other video follows the leading one
  useEffect(() => {
    players.forEach(({ ref, info }, i) => {
      const v = ref.current;
      if (!v || !info.frame_t?.length) return;
      const f = info.fps ?? 1;
      const want = sessionToVideo(t, info.frame_t, f);
      if (!playing) {
        if (!v.paused) v.pause();
        if (Math.abs(v.currentTime - want) > 0.5 / f) v.currentTime = want;
      } else if (i > 0) {
        // the same session seconds per second as the leader, nudged to close the gap: a seek per tick
        // lands late (VP8 seeks take a few hundred ms), which left the follower ~0.6 s behind
        const drift = want - v.currentTime;
        if (Math.abs(drift) > 2 && !v.seeking) v.currentTime = want;
        v.playbackRate = Number(speed) * (fps / f) * (1 + Math.max(-0.5, Math.min(0.5, drift * 0.8)));
        if (v.paused) v.play().catch(() => undefined);
      }
    });
  }, [t, playing, players]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- live camera: sessions whose pipeline is sending frames now
  const liveSources = useQuery({ queryKey: ['liveSources'], queryFn: api.liveSources, enabled: mode === 'live', refetchInterval: 3000 });
  const sourceIds = (liveSources.data ?? []).map((s) => s.session_id);

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

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Live monitoring</Title>
        <Health />
      </Group>
      <Group>
        <Select
          placeholder={sessions.isLoading ? 'Loading sessions…' : 'Pick a session'}
          data={[
            // a live pipeline session that has no event yet is not in /api/sessions
            ...sourceIds.filter((id) => !sessions.data?.some((s) => s.session_id === id)).map((id) => ({ value: id, label: `${id} (live now)` })),
            ...(sessions.data ?? []).map((s) => ({ value: s.session_id, label: `${s.name || s.session_id} (${s.n_events} events)${sourceIds.includes(s.session_id) ? ' · live now' : ''}` })),
          ]}
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
      </Group>
      {mode === 'live' && !!sourceIds.length && !sourceIds.includes(sid ?? '') && (
        <Alert color="blue" p="xs">
          <Group justify="space-between">
            <Text size="sm">A live pipeline is sending camera frames: {sourceIds[0]}</Text>
            <Button size="xs" variant="light" onClick={() => setSid(sourceIds[0])}>Watch it</Button>
          </Group>
        </Alert>
      )}
      <Grid>
        {(['raw', 'annotated'] as const).map((kind) => {
          const q = kind === 'raw' ? rawVideo : annVideo;
          return (
            <Grid.Col key={kind} span={{ base: 12, md: 6 }}>
              <Card withBorder p="xs">
                <Group justify="space-between" mb={6}>
                  <Text fw={600}>{KIND_TITLE[kind]}</Text>
                  <Text size="xs" c="dimmed">
                    {mode === 'live' ? (kind === 'raw' ? 'as received by the detector' : 'detections, track IDs, km/h, rule state')
                      : readyInfo(q.data) ? `${q.data?.source}${master?.info === q.data ? ', leads the clock' : ', synced'}` : ''}
                  </Text>
                </Group>
                {mode === 'live' ? <LiveFeed kind={kind} sid={sid} /> : (
                  <VideoPanel kind={kind} sid={sid} info={q.data} videoRef={kind === 'raw' ? rawRef : annRef}
                    onRequest={() => makeVideo.mutate(kind)} canMake={can(me?.role, 'import_session')} />
                )}
              </Card>
            </Grid.Col>
          );
        })}
      </Grid>
      <Grid>
        <Grid.Col span={{ base: 12, md: 8 }}>
          <Card withBorder p="xs">
            <Group gap={6} mb="xs"><IconMap size={14} /><Text fw={600}>Map</Text></Group>
            {scene.isLoading ? <Loader m="xl" /> : (
              <LaneMap lanes={scene.data?.lanes} zones={mapZones} zoneColor={(z) => zoneColor(z.type)} vehicles={vehicles} trails={trails} pins={pins}
                onPinClick={(id) => setSelected(shownEvents.find((e) => e.event_id === id) ?? null)} height="62vh" />
            )}
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
            <Card withBorder p="xs" style={{ flex: 1 }}>
              <Text fw={600} mb="xs">Events ({shownEvents.length})</Text>
              <ScrollArea h="62vh">
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
