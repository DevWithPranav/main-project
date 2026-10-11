// Configuration > Road configuration: the town as a profile runs it (GET /api/scenes/{town}?profile=),
// coloured by one road attribute; click / shift-click lanes (lane, road section or whole road), edit
// their attributes and add the edits as road.lane_overrides items of the profile (new version via
// PUT /api/profiles/{name}). Pending edits are previewed on the map before saving.

import { ActionIcon, Alert, Badge, Button, Card, Code, Grid, Group, NumberInput, ScrollArea, SegmentedControl, Select, Stack, Text, Textarea, TextInput, Tooltip, useComputedColorScheme } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconArrowBackUp, IconRestore, IconTrash, IconX } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api';
import { ApiError } from '../api/client';
import type { Lane, LaneOverride, Profile, RoadAttribute } from '../api/types';
import { useAuth } from '../auth';
import { useConditions, useScene } from '../hooks';
import { can } from '../lib/permissions';
import { cloneProfile, profileName, unwrapProfile, validateProfile } from '../lib/profiles';
import {
  applyRoadOverrides, clickSeeds, COLOUR_SCHEMES, commonValue, describeItem, FALLBACK_ATTRIBUTES, itemMatches, normaliseAttributes,
  OVERRIDE_KEYS, overridesFor, scopeTarget, selectionFromSeeds, validateItem, type AttrValue, type ColourBy, type OverrideKey, type SelectScope,
} from '../lib/roadEdits';
import LaneMap from './LaneMap';

const NONE = '__none';

function toSelect(v: AttrValue | undefined): string | null {
  if (v === undefined) return null;
  if (v === null) return NONE;
  return String(v);
}

function fromSelect(attr: RoadAttribute, s: string | null): AttrValue | undefined {
  if (s === null) return undefined;
  if (attr.type === 'bool') return s === 'true';
  return s === NONE ? null : s;
}

function AttrRow({ attr, current, mixed, edit, onEdit, disabled, condName }: {
  attr: RoadAttribute; current: AttrValue; mixed: boolean; edit: AttrValue | undefined; onEdit: (v: AttrValue | undefined) => void;
  disabled: boolean; condName: (id: string) => string;
}) {
  const changed = edit !== undefined && (mixed || edit !== current);
  const shown = edit !== undefined ? edit : mixed ? undefined : current;
  const control = attr.type === 'number' ? (
    <NumberInput size="xs" min={1} max={200} value={typeof shown === 'number' ? shown : ''} placeholder={mixed ? 'mixed' : 'none'} disabled={disabled}
      onChange={(x) => onEdit(typeof x === 'number' ? x : x === '' ? undefined : Number(x))} aria-label={attr.label} />
  ) : (
    <Select size="xs" disabled={disabled} placeholder={mixed ? 'mixed' : '—'} value={toSelect(shown)} allowDeselect={false} aria-label={attr.label}
      data={attr.type === 'bool'
        ? [{ value: 'true', label: 'yes' }, { value: 'false', label: 'no' }]
        : (attr.values ?? []).map((v) => ({ value: v === null ? NONE : v, label: v === null ? 'none' : v }))}
      onChange={(s) => onEdit(fromSelect(attr, s))} />
  );
  return (
    <div>
      <Group gap={6} wrap="nowrap" align="center">
        <Text size="xs" w={128} fw={changed ? 700 : 500} c={changed ? 'blue' : undefined} style={{ flexShrink: 0 }}>{attr.label}</Text>
        <div style={{ flex: 1, minWidth: 0 }}>{control}</div>
        <ActionIcon size="sm" variant="subtle" color="gray" disabled={!changed} onClick={() => onEdit(undefined)} aria-label={`reset ${attr.label}`}><IconX size={14} /></ActionIcon>
      </Group>
      <Group gap={4} mt={2} ml={134}>
        <Text size="10px" c="dimmed">{mixed ? 'mixed · ' : ''}drives</Text>
        {attr.drives.map((d) => (
          <Tooltip key={d.condition + d.label} label={d.label || condName(d.condition)} withArrow multiline maw={260}>
            <Badge size="xs" variant="light" color="gray" style={{ cursor: 'help', textTransform: 'none' }}>{d.condition}</Badge>
          </Tooltip>
        ))}
      </Group>
    </div>
  );
}

function errorLines(e: unknown): string[] {
  const msg = e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e);
  return msg.split(/;\s+|\n/).filter(Boolean);
}

export default function RoadEditor() {
  const { me } = useAuth();
  const editable = can(me?.role, 'edit_profile');
  const dark = useComputedColorScheme('light') === 'dark';
  const qc = useQueryClient();
  const { idx: condIdx } = useConditions();

  const scenes = useQuery({ queryKey: ['scenes'], queryFn: api.scenes });
  const towns = useMemo(() => (scenes.data ?? []).map((s) => (typeof s === 'string' ? s : String(s.town ?? s.scene ?? s.name ?? ''))).filter(Boolean).map((s) => s.replace(/\.json$/, '')), [scenes.data]);
  const plist = useQuery({ queryKey: ['profiles'], queryFn: api.profiles });
  const pnames = useMemo(() => (plist.data ?? []).map(profileName), [plist.data]);

  const [town, setTown] = useState<string | null>(null);
  const [pname, setPname] = useState<string | null>(null);
  useEffect(() => { if (!town && towns.length) setTown(towns.includes('Town03') ? 'Town03' : towns[0]); }, [towns, town]);
  useEffect(() => { if (!pname && pnames.length) setPname(pnames.includes('default') ? 'default' : pnames[0]); }, [pnames, pname]);

  const [colourBy, setColourBy] = useState<ColourBy>('road_class');
  const [scope, setScope] = useState<SelectScope>('lane');
  const [seeds, setSeeds] = useState<string[]>([]);
  const [edits, setEdits] = useState<Partial<Record<OverrideKey, AttrValue>>>({});
  const [itemNote, setItemNote] = useState('');
  const [pending, setPending] = useState<{ batch: number; item: LaneOverride }[]>([]);
  const [removed, setRemoved] = useState<Set<number>>(new Set());
  const [note, setNote] = useState('');
  const [applyErr, setApplyErr] = useState<string[]>([]);
  const [saveErr, setSaveErr] = useState<string[]>([]);

  const base = useScene(town);
  const pq = useQuery({ queryKey: ['profile', pname], queryFn: () => api.profile(pname!), enabled: !!pname });
  const profile = unwrapProfile(pq.data);
  const existing = useMemo(() => {
    const lo = (profile?.road as { lane_overrides?: unknown } | undefined)?.lane_overrides;
    return Array.isArray(lo) ? (lo as LaneOverride[]) : [];
  }, [profile]);
  const served = useQuery({
    queryKey: ['sceneProfile', town, pname, pq.dataUpdatedAt],
    queryFn: () => api.sceneForProfile(town!, pname!),
    enabled: !!town && !!pname,
    retry: false,
    staleTime: 60_000,
  });
  const serverApplied = !!served.data && served.data.profile === pname && Array.isArray(served.data.lanes);
  const aq = useQuery({ queryKey: ['roadAttributes'], queryFn: api.roadAttributes, retry: false, staleTime: Infinity });
  const attrs = useMemo(() => {
    const got = normaliseAttributes(aq.data);
    const list = got ?? FALLBACK_ATTRIBUTES;
    return OVERRIDE_KEYS.map((k) => list.find((a) => a.key === k) ?? FALLBACK_ATTRIBUTES.find((a) => a.key === k)!).filter(Boolean);
  }, [aq.data]);
  const attrsFromApi = !!normaliseAttributes(aq.data);

  // new town / profile: start over
  useEffect(() => { setSeeds([]); setEdits({}); setPending([]); setRemoved(new Set()); setApplyErr([]); setSaveErr([]); }, [town, pname]);

  const kept = useMemo(() => existing.filter((_, i) => !removed.has(i)), [existing, removed]);
  const dirty = pending.length > 0 || removed.size > 0;
  const lanes: (Lane & { overridden?: string[] })[] = useMemo(() => {
    if (!dirty && serverApplied) return served.data!.lanes;
    const b = base.data?.lanes ?? [];
    return applyRoadOverrides(b, [...kept, ...pending.map((p) => p.item)]);
  }, [dirty, serverApplied, served.data, base.data, kept, pending]);
  const marked = useMemo(() => new Set(lanes.filter((l) => l.overridden?.length).map((l) => l.id)), [lanes]);
  const pendingIds = useMemo(() => {
    const items = pending.map((p) => p.item);
    return new Set(lanes.filter((l) => items.some((o) => itemMatches(o, [l]))).map((l) => l.id));
  }, [lanes, pending]);

  const selected = useMemo(() => selectionFromSeeds(seeds, scope, lanes), [seeds, scope, lanes]);
  const selLanes = useMemo(() => lanes.filter((l) => selected.has(l.id)), [lanes, selected]);
  useEffect(() => { setEdits({}); setApplyErr([]); }, [seeds, scope]);

  const scheme = COLOUR_SCHEMES[colourBy];
  const laneColor = (l: Lane) => {
    const c = scheme.color(l);
    return c === '#adb5bd' ? (dark ? '#5c5f66' : '#adb5bd') : c;
  };
  const condName = (id: string) => condIdx.get(id)?.name ?? id;

  const effective = useMemo(() => {
    const out: Partial<Record<OverrideKey, AttrValue>> = {};
    for (const [k, v] of Object.entries(edits) as [OverrideKey, AttrValue][]) {
      const cv = commonValue(selLanes, k);
      if (cv.mixed || cv.value !== v) out[k] = v;
    }
    return out;
  }, [edits, selLanes]);

  const apply = () => {
    const items = overridesFor([...selected], scope, effective, itemNote);
    const errs = items.flatMap((o) => validateItem(o, attrs));
    for (const o of items) if (!itemMatches(o, base.data?.lanes ?? lanes)) errs.push(`${o.lane_id} matches no lane in ${town}`);
    setApplyErr(errs);
    if (errs.length || !items.length) return;
    const batch = (pending.at(-1)?.batch ?? 0) + 1;
    setPending([...pending, ...items.map((item) => ({ batch, item }))]);
    setEdits({});
    setItemNote('');
  };
  const undo = () => {
    const last = pending.at(-1)?.batch;
    setPending(pending.filter((p) => p.batch !== last));
  };

  const draft: Profile | null = useMemo(() => {
    if (!profile) return null;
    const p = cloneProfile(profile);
    const lo = [...kept, ...pending.map((x) => x.item)];
    const road = { ...(p.road ?? {}) } as Record<string, unknown>;
    if (lo.length) road.lane_overrides = lo;
    else delete road.lane_overrides;
    p.road = road;
    return p;
  }, [profile, kept, pending]);
  const schemaIssues = useMemo(() => (draft && dirty ? validateProfile(draft).filter((i) => i.level === 'error' && i.path.startsWith('/road')) : []), [draft, dirty]);

  const save = useMutation({
    mutationFn: () => api.saveProfile(pname!, draft!, note.trim()),
    onSuccess: () => {
      notifications.show({ color: 'green', message: `Saved ${pname}: ${pending.length} added, ${removed.size} removed lane override(s)` });
      setPending([]); setRemoved(new Set()); setNote(''); setSaveErr([]);
      qc.invalidateQueries({ queryKey: ['profile', pname] });
      qc.invalidateQueries({ queryKey: ['profileHistory', pname] });
      qc.invalidateQueries({ queryKey: ['sceneProfile', town, pname] });
    },
    onError: (e) => setSaveErr(errorLines(e)),
  });

  const overrideRow = (o: LaneOverride, key: string, right: ReactNode, struck = false) => (
    <Group key={key} justify="space-between" wrap="nowrap" gap={6} py={2} style={{ borderBottom: '1px solid var(--mantine-color-default-border)' }}>
      <div style={{ minWidth: 0, opacity: struck ? 0.5 : 1, textDecoration: struck ? 'line-through' : undefined }}>
        <Text size="xs" fw={600} ff="monospace">{o.lane_id} <Text span size="10px" c="dimmed" ff="inherit">({itemMatches(o, base.data?.lanes ?? lanes)} lanes)</Text></Text>
        <Text size="xs">{describeItem(o, attrs)}</Text>
        {o.note && <Text size="10px" c="dimmed">{o.note}</Text>}
      </div>
      {right}
    </Group>
  );

  return (
    <Grid>
      <Grid.Col span={{ base: 12, md: 7, lg: 8 }}>
        <Group mb="xs" gap="sm" align="flex-end" wrap="wrap">
          <Select label="Town" data={towns} value={town} onChange={setTown} w={130} />
          <Select label="Profile" data={pnames} value={pname} onChange={setPname} w={190} searchable />
          <Select label="Colour by" w={160} value={colourBy} onChange={(v) => v && setColourBy(v as ColourBy)} allowDeselect={false}
            data={(Object.keys(COLOUR_SCHEMES) as ColourBy[]).map((k) => ({ value: k, label: COLOUR_SCHEMES[k].label }))} />
          <div>
            <Text size="sm" fw={500} mb={2}>Select</Text>
            <SegmentedControl value={scope} onChange={(v) => setScope(v as SelectScope)}
              data={[{ value: 'lane', label: 'Lane' }, { value: 'section', label: 'Road section' }, { value: 'road', label: 'Whole road' }]} />
          </div>
        </Group>
        <Group gap={6} mb={6} wrap="wrap" data-testid="road-legend">
          {scheme.legend.map((it) => (
            <Group key={it.label} gap={4} wrap="nowrap">
              <span style={{ display: 'inline-block', width: 18, height: 6, borderRadius: 3, background: it.color === '#adb5bd' && dark ? '#5c5f66' : it.color }} />
              <Text size="xs">{it.label}</Text>
            </Group>
          ))}
          <Group gap={4} wrap="nowrap" ml="sm">
            <span style={{ display: 'inline-block', width: 18, height: 0, borderTop: `2px dashed ${dark ? '#f8f9fa' : '#212529'}` }} />
            <Text size="xs">overridden by the profile{dirty ? ' (incl. pending)' : ''}</Text>
          </Group>
          <Group gap={4} wrap="nowrap">
            <span style={{ display: 'inline-block', width: 18, height: 8, borderRadius: 4, background: '#228be6', opacity: 0.55 }} />
            <Text size="xs">selected</Text>
          </Group>
        </Group>
        <LaneMap lanes={lanes} laneColor={laneColor} selectedLanes={selected} markedLanes={marked} height="68vh"
          onLaneClick={(l, e) => setSeeds((s) => clickSeeds(s, l.id, scope, lanes, e.shiftKey))} />
        <Text size="xs" c="dimmed" mt={4}>
          Click a lane; shift-click adds (or removes) more. Wheel to zoom, drag to pan, double-click to fit.{' '}
          {served.isLoading ? 'Loading the profile view…' : serverApplied
            ? dirty ? 'Showing pending edits, previewed in the browser.' : `As ${pname} runs it (backend view).`
            : `Profile overrides applied in the browser (the backend's ?profile= view is not available${served.error ? `: ${(served.error as Error).message}` : ''}).`}
        </Text>
      </Grid.Col>

      <Grid.Col span={{ base: 12, md: 5, lg: 4 }}>
        <Stack gap="sm">
          {!editable && (
            <Alert color="gray" variant="light" p="xs">Read-only: only a planner or admin can change road attributes ({me?.role ?? 'not logged in'}).</Alert>
          )}
          <Card withBorder p="sm">
            <Group justify="space-between" mb={6}>
              <Text fw={600} size="sm">{selected.size ? `${selected.size} lane${selected.size > 1 ? 's' : ''} selected` : 'No lanes selected'}</Text>
              {selected.size > 0 && <Button size="compact-xs" variant="subtle" onClick={() => setSeeds([])}>Clear</Button>}
            </Group>
            {selected.size > 0 && (
              <Text size="xs" c="dimmed" mb={6} lineClamp={2} ff="monospace">
                {scope === 'lane' ? [...selected].slice(0, 6).join(', ') + (selected.size > 6 ? ' …' : '')
                  : [...new Set([...selected].map((id) => scopeTarget(id, scope)))].join(', ')}
              </Text>
            )}
            {!selected.size ? <Text size="xs" c="dimmed">Click a lane on the map to see and change its attributes.</Text> : (
              <Stack gap={6}>
                {attrs.map((a) => {
                  const cv = commonValue(selLanes, a.key as OverrideKey);
                  return (
                    <AttrRow key={a.key} attr={a} current={cv.value} mixed={cv.mixed} edit={edits[a.key as OverrideKey]} disabled={!editable} condName={condName}
                      onEdit={(v) => setEdits((e) => { const n = { ...e }; if (v === undefined) delete n[a.key as OverrideKey]; else n[a.key as OverrideKey] = v; return n; })} />
                  );
                })}
                {!attrsFromApi && <Text size="10px" c="dimmed">Conditions per attribute: built-in list (GET /api/road/attributes not available).</Text>}
                {editable && (
                  <>
                    <TextInput size="xs" placeholder="Note for this change (optional, kept on the override)" value={itemNote} onChange={(e) => setItemNote(e.currentTarget.value)} />
                    <Button size="xs" onClick={apply} disabled={!Object.keys(effective).length}>
                      Apply to selection ({Object.keys(effective).length} attribute{Object.keys(effective).length === 1 ? '' : 's'})
                    </Button>
                  </>
                )}
                {applyErr.length > 0 && <Alert color="red" p="xs">{applyErr.map((e, i) => <div key={i}><Text size="xs">{e}</Text></div>)}</Alert>}
              </Stack>
            )}
          </Card>

          {editable && (
            <Card withBorder p="sm">
              <Group justify="space-between" mb={4}>
                <Text fw={600} size="sm">Pending changes ({pending.length})</Text>
                <Button size="compact-xs" variant="subtle" leftSection={<IconArrowBackUp size={14} />} disabled={!pending.length} onClick={undo}>Undo</Button>
              </Group>
              {!pending.length ? <Text size="xs" c="dimmed">None. Applied edits show here and on the map before you save.</Text> : (
                <ScrollArea.Autosize mah={180}>
                  {pending.map((p, i) => overrideRow(p.item, `p${i}`,
                    <ActionIcon size="sm" variant="subtle" color="red" aria-label="remove pending" onClick={() => setPending(pending.filter((_, j) => j !== i))}><IconTrash size={14} /></ActionIcon>))}
                </ScrollArea.Autosize>
              )}
              {pendingIds.size > 0 && <Text size="10px" c="dimmed" mt={4}>{pendingIds.size} lanes change.</Text>}
            </Card>
          )}

          <Card withBorder p="sm">
            <Text fw={600} size="sm" mb={4}>Saved overrides in {pname ?? '…'} ({existing.length})</Text>
            {!existing.length ? <Text size="xs" c="dimmed">This profile does not change any lane.</Text> : (
              <ScrollArea.Autosize mah={200}>
                {existing.map((o, i) => overrideRow(o, `e${i}`, editable ? (
                  removed.has(i)
                    ? <ActionIcon size="sm" variant="subtle" aria-label="keep override" onClick={() => { const n = new Set(removed); n.delete(i); setRemoved(n); }}><IconRestore size={14} /></ActionIcon>
                    : <ActionIcon size="sm" variant="subtle" color="red" aria-label="remove override" onClick={() => setRemoved(new Set(removed).add(i))}><IconTrash size={14} /></ActionIcon>
                ) : null, removed.has(i)))}
              </ScrollArea.Autosize>
            )}
          </Card>

          {editable && (
            <Card withBorder p="sm">
              {schemaIssues.length > 0 && (
                <Alert color="yellow" p="xs" mb="xs" title="Not valid against profile.schema.json (the backend decides)">
                  {schemaIssues.slice(0, 5).map((i, n) => <Text key={n} size="xs"><Code>{i.path}</Code> {i.message}</Text>)}
                </Alert>
              )}
              <Text size="xs" mb={4}>{dirty ? `${pending.length} to add, ${removed.size} to remove: saves ${pname} as a new version.` : 'No unsaved changes.'}</Text>
              <Textarea size="xs" placeholder="Why this change? (kept in the profile history)" value={note} onChange={(e) => setNote(e.currentTarget.value)} autosize minRows={2} />
              <Group mt="xs">
                <Button size="xs" onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty || !note.trim() || !draft}>Save to profile</Button>
                <Button size="xs" variant="subtle" disabled={!dirty} onClick={() => { setPending([]); setRemoved(new Set()); setSaveErr([]); }}>Discard</Button>
              </Group>
              {saveErr.length > 0 && (
                <Alert color="red" mt="xs" p="xs" title="The backend refused the save">
                  {saveErr.map((e, i) => <Text key={i} size="xs">{e}</Text>)}
                </Alert>
              )}
            </Card>
          )}
        </Stack>
      </Grid.Col>
    </Grid>
  );
}

