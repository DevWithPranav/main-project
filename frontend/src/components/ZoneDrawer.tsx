// Zone drawer (Configuration > Zones): draw, edit and deactivate the zones that zone-based violation
// rules read (no parking / no stopping, zebra crossing, highway stopping, speed limit, no U-turn),
// through /api/zones. The backend writes each scene's active zones to an engine zone file; a
// profile whose road.zones points at that file makes runs (offline and live) use them.

import { ActionIcon, Alert, Badge, Button, Card, Grid, Group, Loader, Modal, NumberInput, ScrollArea, Select, Stack, Switch, Table, Text, TextInput, Textarea, Tooltip } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconArrowBackUp, IconHistory, IconPencil, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { Profile, ZoneFeature, ZoneInput, ZoneType } from '../api/types';
import { useAuth } from '../auth';
import { useScene, useSessions } from '../hooks';
import { fmtDateTime } from '../lib/format';
import { can } from '../lib/permissions';
import { cloneProfile, profileName, unwrapProfile } from '../lib/profiles';
import { featureToZone, openRing, polygonArea, polygonProblem, ZONE_TYPES, zoneColor, zoneTypeOptions } from '../lib/zones';
import LaneMap, { type MapEditor } from './LaneMap';

type Pt = [number, number];
type Mode = { kind: 'view' } | { kind: 'draw'; type: ZoneType; pts: Pt[]; hover: Pt | null } | { kind: 'shape'; id: string; pts: Pt[]; drag: number | null };
interface FormState { open: boolean; editId: string | null; polygon: Pt[]; name: string; type: ZoneType; grace_s: number | ''; limit_kmh: number | ''; note: string }

const SITE = 'site:';
const ZONE_FILE = (scene: string) => `backend/data/zones/${scene}.json`; // backend config.ZONE_OUT_DIR, repo-relative

export default function ZoneDrawer() {
  const { me } = useAuth();
  const canEdit = can(me?.role, 'edit_zone');
  const qc = useQueryClient();

  // ---- scene: a CARLA town (lane map) or a real-video site (its session's site map)
  const scenes = useQuery({ queryKey: ['scenes'], queryFn: api.scenes, staleTime: Infinity });
  const sessions = useSessions();
  const towns = (scenes.data ?? []).map((s) => (typeof s === 'string' ? s : String(s.town ?? s.scene ?? s.name ?? ''))).filter(Boolean).map((s) => s.replace(/\.json$/, ''));
  const sites = (sessions.data ?? []).filter((s) => s.source === 'video');
  const options = [
    { group: 'CARLA towns', items: towns.map((t) => ({ value: t, label: t })) },
    ...(sites.length ? [{ group: 'Real-video sites', items: sites.map((s) => ({ value: SITE + s.session_id, label: s.name || s.session_id })) }] : []),
  ];
  const [scope, setScope] = useState<string | null>(null);
  useEffect(() => { if (!scope && towns.length) setScope(towns.includes('Town05') ? 'Town05' : towns[0]); }, [towns, scope]);
  const isSite = !!scope?.startsWith(SITE);
  const sceneName = scope ? (isSite ? scope.slice(SITE.length) : scope) : null;
  const townScene = useScene(isSite ? null : scope);
  const siteScene = useQuery({ queryKey: ['sessionScene', sceneName], queryFn: () => api.sessionScene(sceneName!), enabled: isSite && !!sceneName, staleTime: Infinity });
  const map = isSite ? siteScene : townScene;

  // ---- zones
  const [showInactive, setShowInactive] = useState(false);
  const zq = useQuery({
    queryKey: ['zones', sceneName, showInactive],
    queryFn: () => api.zones(sceneName!, { withStatic: true, active: showInactive ? 'all' : 'true' }),
    enabled: !!sceneName,
  });
  const features = zq.data ?? [];
  const byId = useMemo(() => new Map(features.map((f) => [f.properties.id, f])), [features]);
  const [selected, setSelected] = useState<string | null>(null);
  const sel = selected ? byId.get(selected) ?? null : null;
  const refresh = () => qc.invalidateQueries({ queryKey: ['zones'] });

  // ---- drawing / shape editing
  const [mode, setMode] = useState<Mode>({ kind: 'view' });
  const [drawType, setDrawType] = useState<ZoneType>('no_parking');
  useEffect(() => { setMode({ kind: 'view' }); setSelected(null); }, [scope]);
  const emptyForm: FormState = { open: false, editId: null, polygon: [], name: '', type: drawType, grace_s: '', limit_kmh: '', note: '' };
  const [form, setForm] = useState<FormState>(emptyForm);

  const finishDraw = () => {
    if (mode.kind !== 'draw') return;
    // a double click also adds the same point twice: drop points < 0.3 m from the previous one
    const pts = mode.pts.filter((p, i, a) => i === 0 || Math.hypot(p[0] - a[i - 1][0], p[1] - a[i - 1][1]) > 0.3);
    const why = polygonProblem(pts);
    if (why) {
      notifications.show({ color: 'orange', title: 'Zone not finished', message: `The shape ${why}.` });
      return;
    }
    setForm({ ...emptyForm, open: true, polygon: pts, type: mode.type, limit_kmh: mode.type === 'speed' ? 30 : '' });
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // not while typing, nor while a modal (zone form, history) is open: its keys are its own
      const el = e.target as HTMLElement;
      if (form.open || historyId || el?.closest?.('input, textarea, [role="dialog"]')) return;
      if (e.key === 'Enter' && el?.closest?.('button')) return; // Enter on a focused button presses it
      if (e.key === 'Escape') setMode({ kind: 'view' });
      if (mode.kind === 'draw' && e.key === 'Backspace') setMode({ ...mode, pts: mode.pts.slice(0, -1) });
      if (mode.kind === 'draw' && e.key === 'Enter') finishDraw();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }); // eslint-disable-line react-hooks/exhaustive-deps

  const editor: MapEditor | undefined =
    mode.kind === 'draw' ? {
      cursor: 'crosshair',
      onClick: (x, y) => setMode((m) => (m.kind === 'draw' ? { ...m, pts: [...m.pts, [x, y]] } : m)),
      onDoubleClick: () => finishDraw(),
      onMove: (x, y) => setMode((m) => (m.kind === 'draw' ? { ...m, hover: [x, y] } : m)),
    } : mode.kind === 'shape' ? {
      capture: mode.drag !== null,
      onMove: (x, y) => setMode((m) => (m.kind === 'shape' && m.drag !== null ? { ...m, pts: m.pts.map((p, i) => (i === m.drag ? [x, y] : p)) } : m)),
      onUp: () => setMode((m) => (m.kind === 'shape' ? { ...m, drag: null } : m)),
    } : undefined;

  const overlay = (unit: number) => {
    if (mode.kind === 'draw') {
      const c = ZONE_TYPES[mode.type].color;
      const line = [...mode.pts, ...(mode.hover ? [mode.hover] : [])];
      return (
        <g pointerEvents="none">
          {line.length > 2 && <polygon points={line.map((p) => p.join(',')).join(' ')} fill={c} fillOpacity={0.18} stroke="none" />}
          {line.length > 1 && <polyline points={line.map((p) => p.join(',')).join(' ')} fill="none" stroke={c} strokeWidth={unit * 2} strokeDasharray={`${unit * 5} ${unit * 3}`} />}
          {mode.pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r={unit * 4} fill={i === 0 ? '#228be6' : c} stroke="#fff" strokeWidth={unit} />)}
        </g>
      );
    }
    if (mode.kind === 'shape') {
      const c = zoneColor(String(byId.get(mode.id)?.properties.type));
      return (
        <g>
          <polygon points={mode.pts.map((p) => p.join(',')).join(' ')} fill={c} fillOpacity={0.3} stroke="#228be6" strokeWidth={unit * 2} pointerEvents="none" />
          {mode.pts.map((p, i) => (
            <circle key={i} cx={p[0]} cy={p[1]} r={unit * 6} fill="#228be6" stroke="#fff" strokeWidth={unit * 1.5} style={{ cursor: 'move' }}
              onPointerDown={(e) => { e.stopPropagation(); setMode((m) => (m.kind === 'shape' ? { ...m, drag: i } : m)); }} />
          ))}
        </g>
      );
    }
    return null;
  };

  // ---- saving
  const onError = (title: string) => (e: Error) => notifications.show({ color: 'red', title, message: e.message });
  const saved = (z: ZoneFeature, what: string) => {
    notifications.show({ color: 'green', title: `Zone ${what}`, message: `${z.properties.name ?? z.properties.id} (v${z.properties.version})${z.file ? ` · engine file ${z.file}` : ''}` });
    refresh();
  };
  const create = useMutation({
    mutationFn: (z: ZoneInput) => api.createZone(z),
    onSuccess: (z) => { saved(z, 'saved'); setForm(emptyForm); setMode({ kind: 'view' }); setSelected(z.properties.id); },
    onError: onError('Zone not saved'),
  });
  const update = useMutation({
    mutationFn: ({ id, z }: { id: string; z: ZoneInput }) => api.updateZone(id, z),
    onSuccess: (z) => { saved(z, 'updated'); setForm(emptyForm); setMode({ kind: 'view' }); },
    onError: onError('Zone not updated'),
  });
  const deactivate = useMutation({
    mutationFn: (id: string) => api.deactivateZone(id),
    onSuccess: (z) => { saved(z, 'deactivated'); setSelected(null); },
    onError: onError('Zone not deactivated'),
  });
  const reactivate = useMutation({
    mutationFn: (id: string) => api.updateZone(id, { active: true, note: 'reactivated in the dashboard' }),
    onSuccess: (z) => saved(z, 'reactivated'),
    onError: onError('Zone not reactivated'),
  });

  const submitForm = () => {
    const param = ZONE_TYPES[form.type].param;
    const body: ZoneInput = {
      name: form.name.trim(), type: form.type, note: form.note.trim() || undefined,
      grace_s: param === 'grace_s' && form.grace_s !== '' ? Number(form.grace_s) : null,
      limit_kmh: param === 'limit_kmh' && form.limit_kmh !== '' ? Number(form.limit_kmh) : null,
    };
    if (form.editId) update.mutate({ id: form.editId, z: body });
    else create.mutate({ ...body, scene: sceneName!, polygon: form.polygon });
  };
  const openSettings = (f: ZoneFeature) => setForm({
    open: true, editId: f.properties.id, polygon: [], name: f.properties.name ?? '', type: f.properties.type as ZoneType,
    grace_s: f.properties.grace_s ?? '', limit_kmh: f.properties.limit_kmh ?? '', note: '',
  });

  // ---- history
  const [historyId, setHistoryId] = useState<string | null>(null);
  const hist = useQuery({ queryKey: ['zoneHistory', historyId], queryFn: () => api.zoneHistory(historyId!), enabled: !!historyId });

  const apiZones = features.filter((f) => f.properties.source === 'api');
  const fileZones = features.filter((f) => f.properties.source !== 'api');
  const mapZones = features.map(featureToZone).filter((z) => !(mode.kind === 'shape' && z.id === mode.id));
  const colourOf = (id: string, type: string) => (byId.get(id)?.properties.active === false ? '#868e96' : zoneColor(type));

  return (
    <Grid>
      <Grid.Col span={{ base: 12, md: 8 }}>
        <Group mb="xs" justify="space-between">
          <Group>
            <Select data={options} value={scope} onChange={setScope} w={230} aria-label="Scene" searchable />
            {canEdit && mode.kind === 'view' && (
              <>
                <Select data={zoneTypeOptions} value={drawType} onChange={(v) => v && setDrawType(v as ZoneType)} w={220} aria-label="Zone type" />
                <Button leftSection={<IconPlus size={14} />} onClick={() => { setSelected(null); setMode({ kind: 'draw', type: drawType, pts: [], hover: null }); }} disabled={!map.data}>
                  Draw zone
                </Button>
              </>
            )}
            {mode.kind === 'draw' && (
              <>
                <Badge color={ZONE_TYPES[mode.type].color} variant="light" size="lg">{ZONE_TYPES[mode.type].label}</Badge>
                <Text size="sm" c="dimmed">{mode.pts.length} point{mode.pts.length === 1 ? '' : 's'}</Text>
                <Button size="xs" variant="light" leftSection={<IconArrowBackUp size={14} />} disabled={!mode.pts.length} onClick={() => setMode({ ...mode, pts: mode.pts.slice(0, -1) })}>Undo point</Button>
                <Button size="xs" onClick={finishDraw} disabled={mode.pts.length < 3}>Finish</Button>
                <Button size="xs" variant="subtle" color="gray" onClick={() => setMode({ kind: 'view' })}>Cancel</Button>
              </>
            )}
            {mode.kind === 'shape' && (
              <>
                <Text size="sm">Drag the corners</Text>
                <Button size="xs" loading={update.isPending} onClick={() => {
                  const why = polygonProblem(mode.pts);
                  if (why) notifications.show({ color: 'orange', title: 'Shape not saved', message: `The shape ${why}.` });
                  else update.mutate({ id: mode.id, z: { polygon: mode.pts, note: 'shape edited in the dashboard' } });
                }}>Save shape</Button>
                <Button size="xs" variant="subtle" color="gray" onClick={() => setMode({ kind: 'view' })}>Cancel</Button>
              </>
            )}
          </Group>
          <Group gap={4}>
            {zoneTypeOptions.map((o) => <Badge key={o.value} variant="dot" color={zoneColor(o.value)}>{o.label}</Badge>)}
          </Group>
        </Group>
        {mode.kind === 'draw' && (
          <Text size="xs" c="dimmed" mb={4}>
            Click to add corners. Double-click, Enter or Finish closes the shape; Backspace removes the last corner; Esc cancels. Drag to pan, wheel to zoom.
          </Text>
        )}
        {map.isLoading ? <Loader m="xl" /> : map.isError ? <Alert color="red">No map for {sceneName}.</Alert> : (
          <LaneMap lanes={map.data?.lanes} zones={mapZones} zoneColor={(z) => colourOf(z.id, z.type)} selectedZone={selected}
            onZoneClick={mode.kind === 'view' ? (z) => setSelected(z.id) : undefined} editor={editor} height="68vh">
            {overlay}
          </LaneMap>
        )}
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 4 }}>
        <Stack gap="sm">
          {sel && (
            <Card withBorder>
              <Group justify="space-between" mb={6}>
                <Text fw={600}>{sel.properties.name ?? sel.properties.id}</Text>
                <Badge color={zoneColor(String(sel.properties.type))} variant="light">{ZONE_TYPES[sel.properties.type as ZoneType]?.label ?? sel.properties.type}</Badge>
              </Group>
              <Text size="xs" c="dimmed" mb={6}>Drives: {ZONE_TYPES[sel.properties.type as ZoneType]?.drives ?? '-'}</Text>
              <Table withRowBorders={false} verticalSpacing={1} fz="xs">
                <Table.Tbody>
                  {([
                    ['id', sel.properties.id],
                    ['area', `${(sel.properties.area_m2 ?? polygonArea(openRing(sel.geometry.coordinates[0]))).toFixed(0)} m²`],
                    sel.properties.grace_s != null && ['wait before flagging', `${sel.properties.grace_s} s`],
                    sel.properties.limit_kmh != null && ['speed limit', `${sel.properties.limit_kmh} km/h`],
                    ['status', sel.properties.source !== 'api' ? 'from the map file (read-only)' : sel.properties.active ? `active, v${sel.properties.version}` : `deactivated, v${sel.properties.version}`],
                    sel.properties.updated_by && ['last change', `${sel.properties.updated_by}, ${fmtDateTime(sel.properties.updated_at ?? '')}`],
                  ].filter(Boolean) as [string, string][]).map(([k, v]) => (
                    <Table.Tr key={k}><Table.Td c="dimmed" w={130}>{k}</Table.Td><Table.Td>{v}</Table.Td></Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              {sel.properties.source === 'api' && mode.kind === 'view' && (
                <Group gap={6} mt="sm">
                  {canEdit && sel.properties.active && (
                    <>
                      <Button size="xs" variant="light" leftSection={<IconPencil size={14} />} onClick={() => openSettings(sel)}>Settings</Button>
                      <Button size="xs" variant="light" onClick={() => setMode({ kind: 'shape', id: sel.properties.id, pts: openRing(sel.geometry.coordinates[0]), drag: null })}>Edit shape</Button>
                    </>
                  )}
                  <Button size="xs" variant="subtle" leftSection={<IconHistory size={14} />} onClick={() => setHistoryId(sel.properties.id)}>History</Button>
                  {can(me?.role, 'deactivate_zone') && (sel.properties.active ? (
                    <Button size="xs" variant="subtle" color="red" leftSection={<IconTrash size={14} />} loading={deactivate.isPending}
                      onClick={() => window.confirm(`Deactivate zone "${sel.properties.name}"? Its history is kept.`) && deactivate.mutate(sel.properties.id)}>Deactivate</Button>
                  ) : (
                    <Button size="xs" variant="subtle" loading={reactivate.isPending} onClick={() => reactivate.mutate(sel.properties.id)}>Reactivate</Button>
                  ))}
                </Group>
              )}
            </Card>
          )}

          <Card withBorder>
            <Group justify="space-between" mb={6}>
              <Text fw={600}>Zones ({apiZones.length})</Text>
              <Switch size="xs" label="show deactivated" checked={showInactive} onChange={(e) => setShowInactive(e.currentTarget.checked)} />
            </Group>
            {zq.isLoading ? <Loader size="sm" /> : (
              <ScrollArea.Autosize mah="30vh">
                <Stack gap={4}>
                  {!apiZones.length && <Text size="sm" c="dimmed">{canEdit ? 'No drawn zones yet: pick a type and press Draw zone.' : 'No drawn zones.'}</Text>}
                  {apiZones.map((f) => (
                    <Card key={f.id} withBorder p={6} style={{ cursor: 'pointer', borderColor: f.properties.id === selected ? '#228be6' : undefined, opacity: f.properties.active ? 1 : 0.55 }}
                      onClick={() => setSelected(f.properties.id)}>
                      <Group justify="space-between" wrap="nowrap">
                        <Group gap={6} wrap="nowrap">
                          <div style={{ width: 10, height: 10, borderRadius: 2, background: zoneColor(String(f.properties.type)), flex: 'none' }} />
                          <Text size="sm" truncate>{f.properties.name}</Text>
                        </Group>
                        <Text size="xs" c="dimmed">
                          {f.properties.limit_kmh != null ? `${f.properties.limit_kmh} km/h` : f.properties.grace_s != null ? `${f.properties.grace_s} s` : ''} v{f.properties.version}
                        </Text>
                      </Group>
                    </Card>
                  ))}
                  {!!fileZones.length && <Text size="xs" c="dimmed" mt={4}>+ {fileZones.length} zone(s) from the map file (dashed, read-only)</Text>}
                </Stack>
              </ScrollArea.Autosize>
            )}
          </Card>

          {sceneName && <UseInProfile scene={sceneName} hasZones={apiZones.some((f) => f.properties.active)} canEdit={can(me?.role, 'edit_profile')} />}
        </Stack>
      </Grid.Col>

      <Modal opened={form.open} onClose={() => setForm(emptyForm)} title={form.editId ? 'Zone settings' : 'New zone'} centered>
        <Stack gap="sm">
          <TextInput label="Name" placeholder="e.g. School entrance no stopping" value={form.name} onChange={(e) => setForm({ ...form, name: e.currentTarget.value })} required data-autofocus />
          <Select label="Type" data={zoneTypeOptions} value={form.type} onChange={(v) => v && setForm({ ...form, type: v as ZoneType })} />
          <Text size="xs" c="dimmed" mt={-6}>Drives: {ZONE_TYPES[form.type].drives}</Text>
          {ZONE_TYPES[form.type].param === 'grace_s' && (
            <NumberInput label="Wait before flagging (s)" description="Empty: the rule's default from the profile. A few seconds makes it a no-stopping zone."
              min={0} max={3600} value={form.grace_s} onChange={(v) => setForm({ ...form, grace_s: v === '' ? '' : Number(v) })} />
          )}
          {ZONE_TYPES[form.type].param === 'limit_kmh' && (
            <NumberInput label="Speed limit (km/h)" min={1} max={200} required value={form.limit_kmh} onChange={(v) => setForm({ ...form, limit_kmh: v === '' ? '' : Number(v) })} />
          )}
          {!form.editId && <Text size="xs" c="dimmed">{form.polygon.length} corners, {polygonArea(form.polygon).toFixed(0)} m² on {sceneName}</Text>}
          <Textarea label="Note (why)" autosize minRows={2} value={form.note} onChange={(e) => setForm({ ...form, note: e.currentTarget.value })} />
          <Group justify="flex-end">
            <Button variant="subtle" color="gray" onClick={() => setForm(emptyForm)}>Cancel</Button>
            <Button onClick={submitForm} loading={create.isPending || update.isPending}
              disabled={!form.name.trim() || (form.type === 'speed' && form.limit_kmh === '')}>Save zone</Button>
          </Group>
        </Stack>
      </Modal>

      <Modal opened={!!historyId} onClose={() => setHistoryId(null)} title="Zone history" size="lg" centered>
        {hist.isLoading ? <Loader size="sm" /> : (
          <Table striped fz="sm">
            <Table.Thead><Table.Tr><Table.Th>v</Table.Th><Table.Th>change</Table.Th><Table.Th>by</Table.Th><Table.Th>when</Table.Th><Table.Th>note</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>
              {(hist.data ?? []).map((h) => (
                <Table.Tr key={h.version}><Table.Td>{h.version}</Table.Td><Table.Td>{h.action}</Table.Td><Table.Td>{h.by}</Table.Td><Table.Td>{fmtDateTime(h.at)}</Table.Td><Table.Td>{h.note ?? '-'}</Table.Td></Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Modal>
    </Grid>
  );
}

/** Which profiles run with this scene's drawn zones (road.zones = its engine file), and a switch to add one. */
function UseInProfile({ scene, hasZones, canEdit }: { scene: string; hasZones: boolean; canEdit: boolean }) {
  const qc = useQueryClient();
  const file = ZONE_FILE(scene);
  const list = useQuery({ queryKey: ['profiles'], queryFn: api.profiles });
  const profiles = (list.data ?? []).map((p) => ({ name: profileName(p), p: unwrapProfile(p) })).filter((x): x is { name: string; p: Profile } => !!x.p);
  const using = profiles.filter((x) => (x.p.road as { zones?: string } | undefined)?.zones === file).map((x) => x.name);
  const [pick, setPick] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: async ({ name, on }: { name: string; on: boolean }) => {
      const p = cloneProfile(profiles.find((x) => x.name === name)!.p);
      const road = { ...(p.road ?? {}) } as Record<string, unknown>;
      if (on) road.zones = file;
      else delete road.zones;
      return api.saveProfile(name, { ...p, road }, on ? `use the zones drawn for ${scene} (${file})` : `stop using the zones drawn for ${scene}`);
    },
    onSuccess: (_, v) => {
      notifications.show({ color: 'green', title: 'Profile saved', message: `${v.name} ${v.on ? 'now runs with' : 'no longer uses'} the ${scene} zones.` });
      qc.invalidateQueries({ queryKey: ['profiles'] });
      qc.invalidateQueries({ queryKey: ['profile'] });
    },
    onError: (e: Error) => notifications.show({ color: 'red', title: 'Profile not saved', message: e.message }),
  });
  return (
    <Card withBorder>
      <Text fw={600} mb={4}>Use in the violation engine</Text>
      <Text size="xs" c="dimmed" mb="xs">
        Runs use the drawn zones when their profile points at <code>{file}</code> (offline runs and the live pipeline read it when they start).
      </Text>
      {using.length ? (
        <Group gap={4} mb="xs">
          <Text size="sm">Used by:</Text>
          {using.map((n) => (
            <Badge key={n} variant="light" rightSection={canEdit ? (
              <Tooltip label="Stop using"><ActionIcon size="xs" variant="transparent" onClick={() => save.mutate({ name: n, on: false })}>×</ActionIcon></Tooltip>
            ) : undefined}>{n}</Badge>
          ))}
        </Group>
      ) : <Text size="sm" mb="xs">No profile uses them yet.</Text>}
      {canEdit && (
        <Group gap={6}>
          <Select size="xs" placeholder="Profile" data={profiles.map((x) => x.name).filter((n) => !using.includes(n))} value={pick} onChange={setPick} w={170} />
          <Button size="xs" variant="light" disabled={!pick || !hasZones} loading={save.isPending} onClick={() => pick && save.mutate({ name: pick, on: true })}>Use zones</Button>
        </Group>
      )}
      {canEdit && !hasZones && <Text size="xs" c="dimmed" mt={4}>Draw a zone first: the zone file is made with the first one.</Text>}
    </Card>
  );
}
