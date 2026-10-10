// Live monitoring: vehicles on the lane map coloured by rule state, the event feed and system health.
// "Live" listens to WS /api/ws/live; "Replay" plays an imported session from its trajectories,
// with events appearing at their flag time, so the page works without a live source.

import { ActionIcon, Badge, Card, Grid, Group, Loader, ScrollArea, SegmentedControl, Select, Slider, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconPlayerPause, IconPlayerPlay, IconPlayerSkipBack } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, openLive, type LiveStatus } from '../api';
import { eventTime, isAnomaly, type TrafficEvent, type VehicleState } from '../api/types';
import EventDrawer from '../components/EventDrawer';
import LaneMap, { type MapPoint } from '../components/LaneMap';
import { sessionTown, useScene, useSessions } from '../hooks';
import { eventSummary, fmtSimTime, TYPE_COLORS, typeLabel } from '../lib/format';

const STATE_COLORS = { ok: '#40c057', checking: '#fab005', flagged: '#fa5252' } as const;
const MANTINE_HEX: Record<string, string> = {
  red: '#fa5252', orange: '#fd7e14', yellow: '#fab005', grape: '#be4bdb', violet: '#7950f2', blue: '#228be6',
  cyan: '#15aabf', teal: '#12b886', green: '#40c057', pink: '#e64980', indigo: '#4c6ef5', gray: '#868e96', lime: '#82c91e',
};
const hex = (c: string | undefined) => MANTINE_HEX[c ?? 'gray'] ?? c ?? '#868e96';

function Health() {
  const q = useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 10_000 });
  if (q.isError) return <Badge color="red">backend unreachable</Badge>;
  return (
    <Group gap={4}>
      {Object.entries(q.data ?? {}).map(([k, v]) => (
        <Badge key={k} color={v === 'ok' ? 'green' : 'red'} variant="light" title={v}>{k}</Badge>
      ))}
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

export default function LivePage() {
  const sessions = useSessions();
  const [sid, setSid] = useState<string | null>(null);
  const [mode, setMode] = useState<'replay' | 'live'>('replay');
  const [selected, setSelected] = useState<TrafficEvent | null>(null);
  useEffect(() => {
    if (!sid && sessions.data?.length) setSid(sessions.data[0].session_id);
  }, [sessions.data, sid]);
  const session = sessions.data?.find((s) => s.session_id === sid);
  const scene = useScene(sessionTown(session));

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
  const last = useRef<number | null>(null);
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const step = (now: number) => {
      if (last.current != null) setT((x) => Math.min(tMax, x + ((now - last.current!) / 1000) * Number(speed)));
      last.current = now;
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => { cancelAnimationFrame(raf); last.current = null; };
  }, [playing, speed, tMax]);
  useEffect(() => { if (t >= tMax && playing) setPlaying(false); }, [t, tMax, playing]);
  useEffect(() => { setT(tMin); setPlaying(false); }, [sid, tMin]);

  // ---- live
  const [liveVehicles, setLiveVehicles] = useState<VehicleState[]>([]);
  const [liveEvents, setLiveEvents] = useState<TrafficEvent[]>([]);
  const [status, setStatus] = useState<LiveStatus>('closed');
  useEffect(() => {
    if (mode !== 'live') return;
    setLiveVehicles([]); setLiveEvents([]);
    return openLive(sid, (m) => {
      if (m.type === 'vehicles') setLiveVehicles(m.items);
      else {
        setLiveEvents((l) => [m.event, ...l].slice(0, 200));
        notifications.show({ color: 'red', title: typeLabel(m.event.type), message: eventSummary(m.event), autoClose: 4000 });
      }
    }, setStatus);
  }, [mode, sid]);

  const events = evq.data?.items ?? [];
  const shownEvents = mode === 'live' ? liveEvents : events.filter((e) => eventTime(e) <= t).sort((a, b) => eventTime(b) - eventTime(a));

  const vehicles: MapPoint[] = useMemo(() => {
    if (mode === 'live')
      return liveVehicles.map((v) => ({ id: v.track_id, x: v.x, y: v.y, heading_deg: v.heading_deg, color: STATE_COLORS[v.state], label: `${Math.round(v.speed_kmh)}` }));
    const out: MapPoint[] = [];
    for (const [tid, s] of Object.entries(traj.data ?? {})) {
      const p = at(s, t);
      if (!p) continue;
      const evs = events.filter((e) => !isAnomaly(e) && e.track_ids.includes(Number(tid)));
      let state: keyof typeof STATE_COLORS = 'ok';
      for (const e of evs) {
        if (isAnomaly(e)) continue;
        if (e.flag_s <= t && t <= (e.end_s ?? e.flag_s + 8)) state = 'flagged';
        else if (state === 'ok' && e.start_s <= t && t < e.flag_s) state = 'checking';
      }
      out.push({ id: tid, x: p.x, y: p.y, heading_deg: p.h, color: STATE_COLORS[state], label: `${Math.round(p.v)}` });
    }
    return out;
  }, [mode, liveVehicles, traj.data, t, events]);

  const pins: MapPoint[] = shownEvents.slice(0, 60).map((e) => ({ id: e.event_id, x: e.x, y: e.y, color: hex(TYPE_COLORS[e.type]), label: eventSummary(e) }));

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
        <SegmentedControl value={mode} onChange={(v) => setMode(v as 'replay' | 'live')} data={[{ value: 'replay', label: 'Replay' }, { value: 'live', label: 'Live' }]} />
        {mode === 'live' && <Badge color={status === 'open' ? 'green' : status === 'connecting' ? 'yellow' : 'red'}>{status}</Badge>}
        <Group gap={6}>
          {Object.entries(STATE_COLORS).map(([k, c]) => (
            <Badge key={k} variant="dot" color={c}>{k}</Badge>
          ))}
        </Group>
      </Group>
      <Grid>
        <Grid.Col span={{ base: 12, md: 8 }}>
          <Card withBorder p="xs">
            {scene.isLoading ? <Loader m="xl" /> : (
              <LaneMap lanes={scene.data?.lanes} zones={scene.data?.zones} vehicles={vehicles} pins={pins}
                onPinClick={(id) => setSelected(shownEvents.find((e) => e.event_id === id) ?? null)} height="62vh" />
            )}
            {mode === 'replay' && (
              <Group mt="xs" wrap="nowrap">
                <ActionIcon variant="light" onClick={() => setT(tMin)}><IconPlayerSkipBack size={16} /></ActionIcon>
                <ActionIcon variant="filled" onClick={() => setPlaying((p) => !p)} disabled={!tMax}>
                  {playing ? <IconPlayerPause size={16} /> : <IconPlayerPlay size={16} />}
                </ActionIcon>
                <Slider style={{ flex: 1 }} min={tMin} max={Math.max(tMax, tMin + 1)} step={0.1} value={t} onChange={setT} label={fmtSimTime} />
                <Text size="sm" w={110} ta="right">{fmtSimTime(t)} / {fmtSimTime(tMax)}</Text>
                <Select w={80} data={['1', '2', '4', '8']} value={speed} onChange={(v) => setSpeed(v ?? '1')} />
              </Group>
            )}
            {mode === 'replay' && traj.isFetching && <Text size="xs" c="dimmed">loading trajectories…</Text>}
          </Card>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 4 }}>
          <Card withBorder p="xs" h="100%">
            <Text fw={600} mb="xs">Events ({shownEvents.length})</Text>
            <ScrollArea h="62vh">
              <Stack gap={6}>
                {shownEvents.length === 0 && <Text size="sm" c="dimmed">{mode === 'live' ? 'Waiting for events…' : 'No events yet at this time.'}</Text>}
                {shownEvents.map((e) => (
                  <Card key={e.event_id} withBorder p={8} style={{ cursor: 'pointer' }} onClick={() => setSelected(e)}>
                    <Group justify="space-between" wrap="nowrap">
                      <Badge color={TYPE_COLORS[e.type] ?? 'gray'} size="sm">{typeLabel(e.type)}</Badge>
                      <Text size="xs" c="dimmed">{fmtSimTime(eventTime(e))}</Text>
                    </Group>
                    <Text size="xs" mt={4}>{eventSummary(e)}</Text>
                  </Card>
                ))}
              </Stack>
            </ScrollArea>
          </Card>
        </Grid.Col>
      </Grid>
      <EventDrawer event={selected} onClose={() => setSelected(null)} />
    </Stack>
  );
}
