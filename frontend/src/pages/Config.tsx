// Configuration: profiles (violation on/off + thresholds, conditions, modules), validated against
// schemas/profile.schema.json before saving a new version with a note; version history; road view.

import { Accordion, Alert, Badge, Button, Card, Code, Grid, Group, JsonInput, NumberInput, Select, SimpleGrid, Stack, Switch, Table, Tabs, Text, Textarea, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { Lane, Profile, ViolationType } from '../api/types';
import { useAuth } from '../auth';
import LaneMap from '../components/LaneMap';
import { useConditions, useScene } from '../hooks';
import { fmtDateTime, typeLabel } from '../lib/format';
import { can } from '../lib/permissions';
import { cloneProfile, DEFAULT_PROFILE, diffPaths, paramLabel, profileName, unwrapProfile, validateProfile } from '../lib/profiles';

function ParamInput({ k, v, onChange, disabled }: { k: string; v: unknown; onChange: (v: unknown) => void; disabled: boolean }) {
  if (typeof v === 'boolean') return <Switch label={paramLabel(k)} checked={v} onChange={(e) => onChange(e.currentTarget.checked)} disabled={disabled} />;
  if (typeof v === 'number')
    return <NumberInput label={paramLabel(k)} value={v} onChange={(x) => onChange(typeof x === 'number' ? x : Number(x))} decimalScale={4} disabled={disabled} size="xs" />;
  return (
    <JsonInput label={paramLabel(k)} value={JSON.stringify(v, null, 1)} size="xs" autosize maxRows={6} disabled={disabled}
      onChange={(s) => { try { onChange(JSON.parse(s)); } catch { /* keep editing */ } }} />
  );
}

function ProfileEditor({ name }: { name: string }) {
  const { me } = useAuth();
  const editable = can(me?.role, 'edit_profile');
  const qc = useQueryClient();
  const { data: conds } = useConditions();
  const pq = useQuery({ queryKey: ['profile', name], queryFn: () => api.profile(name) });
  const hq = useQuery({ queryKey: ['profileHistory', name], queryFn: () => api.profileHistory(name) });
  const original = unwrapProfile(pq.data);
  const [draft, setDraft] = useState<Profile | null>(null);
  const [note, setNote] = useState('');
  useEffect(() => setDraft(original ? cloneProfile(original) : null), [pq.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const issues = useMemo(() => (draft ? validateProfile(draft) : []), [draft]);
  const changed = useMemo(() => (draft && original ? diffPaths(original, draft) : []), [draft, original]);
  const save = useMutation({
    mutationFn: () => api.saveProfile(name, draft!, note),
    onSuccess: () => {
      notifications.show({ color: 'green', message: `Saved ${name} (${changed.length} changes)` });
      setNote('');
      qc.invalidateQueries({ queryKey: ['profile', name] });
      qc.invalidateQueries({ queryKey: ['profileHistory', name] });
    },
    onError: (e: Error) => notifications.show({ color: 'red', title: 'Save failed', message: e.message }),
  });

  if (!draft) return <Text c="dimmed">{pq.isLoading ? 'Loading…' : 'Profile not found.'}</Text>;
  const viol = draft.violations ?? {};
  const types = Object.keys({ ...DEFAULT_PROFILE.violations, ...viol }) as ViolationType[];
  const setViol = (t: ViolationType, patch: object) =>
    setDraft({ ...draft, violations: { ...viol, [t]: { ...(viol[t] ?? {}), ...patch } } });

  return (
    <Grid>
      <Grid.Col span={{ base: 12, lg: 8 }}>
        <Stack>
          {draft.description && <Text size="sm" c="dimmed">{draft.description}</Text>}
          <Accordion variant="separated" multiple>
            {types.map((t) => {
              const cfg = viol[t] ?? DEFAULT_PROFILE.violations?.[t] ?? {};
              const params = { ...(DEFAULT_PROFILE.violations?.[t]?.params ?? {}), ...(cfg.params ?? {}) };
              return (
                <Accordion.Item key={t} value={t}>
                  <Accordion.Control>
                    <Group justify="space-between" pr="md">
                      <Text fw={500}>{typeLabel(t)}</Text>
                      <Badge color={cfg.enabled === false ? 'gray' : 'green'} variant="light">{cfg.enabled === false ? 'off' : 'on'}</Badge>
                    </Group>
                  </Accordion.Control>
                  <Accordion.Panel>
                    <Switch mb="sm" label="Enabled" checked={cfg.enabled !== false} disabled={!editable} onChange={(e) => setViol(t, { enabled: e.currentTarget.checked })} />
                    <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }}>
                      {Object.entries(params).map(([k, v]) => (
                        <ParamInput key={k} k={k} v={v} disabled={!editable} onChange={(nv) => setViol(t, { params: { ...params, [k]: nv } })} />
                      ))}
                    </SimpleGrid>
                  </Accordion.Panel>
                </Accordion.Item>
              );
            })}
            <Accordion.Item value="conditions">
              <Accordion.Control><Text fw={500}>Conditions on/off</Text></Accordion.Control>
              <Accordion.Panel>
                <SimpleGrid cols={{ base: 1, sm: 2, md: 3 }}>
                  {(conds?.conditions ?? []).filter((c) => !c.alias_of).map((c) => (
                    <Switch key={c.id} size="xs" disabled={!editable}
                      label={<Text size="xs" component="span">{c.id} {c.name} {c.status !== 'built' && <Badge size="xs" variant="light" color="gray">{c.status}</Badge>}</Text>}
                      checked={draft.conditions?.[c.id] !== false}
                      onChange={(e) => setDraft({ ...draft, conditions: { ...(draft.conditions ?? {}), [c.id]: e.currentTarget.checked } })} />
                  ))}
                </SimpleGrid>
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="model">
              <Accordion.Control><Text fw={500}>Model, modules and road settings</Text></Accordion.Control>
              <Accordion.Panel>
                {(['model', 'modules', 'road', 'place_memory', 'applies_to'] as const).map((k) => (
                  <JsonInput key={k} label={k} mb="xs" autosize maxRows={10} disabled={!editable} value={JSON.stringify(draft[k] ?? {}, null, 1)}
                    onChange={(s) => { try { setDraft({ ...draft, [k]: JSON.parse(s) }); } catch { /* keep editing */ } }} />
                ))}
              </Accordion.Panel>
            </Accordion.Item>
          </Accordion>
          {editable && (
            <Card withBorder>
              {issues.length > 0 && (
                <Alert color="red" mb="sm" title="Not valid against profile.schema.json">
                  {issues.slice(0, 6).map((i, n) => <div key={n}><Code>{i.path || '/'}</Code> {i.message}</div>)}
                </Alert>
              )}
              <Text size="sm" mb={4}>{changed.length ? `${changed.length} change(s): ${changed.slice(0, 5).join(', ')}${changed.length > 5 ? ' …' : ''}` : 'No changes.'}</Text>
              <Textarea placeholder="Why this change? (kept in the history)" value={note} onChange={(e) => setNote(e.currentTarget.value)} autosize minRows={2} />
              <Group mt="sm">
                <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!changed.length || issues.length > 0 || !note.trim()}>Save new version</Button>
                <Button variant="subtle" onClick={() => original && setDraft(cloneProfile(original))} disabled={!changed.length}>Reset</Button>
              </Group>
            </Card>
          )}
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, lg: 4 }}>
        <Card withBorder>
          <Text fw={600} mb="xs">History</Text>
          {!hq.data?.length ? <Text size="sm" c="dimmed">No saved versions yet.</Text> : (
            <Stack gap={6}>
              {hq.data.map((h, i) => (
                <Card key={i} withBorder p={8}>
                  <Group justify="space-between"><Badge variant="light">v{h.version ?? '?'}</Badge><Text size="xs" c="dimmed">{fmtDateTime((h.at ?? h.when) as string)}</Text></Group>
                  <Text size="xs" mt={4}>{h.by ?? h.who ?? '?'}: {h.note ?? '-'}</Text>
                </Card>
              ))}
            </Stack>
          )}
        </Card>
      </Grid.Col>
    </Grid>
  );
}

function RoadView() {
  const scenes = useQuery({ queryKey: ['scenes'], queryFn: api.scenes });
  const names = (scenes.data ?? []).map((s) => (typeof s === 'string' ? s : String(s.town ?? s.scene ?? s.name ?? ''))).filter(Boolean).map((s) => s.replace(/\.json$/, ''));
  const [town, setTown] = useState<string | null>(null);
  useEffect(() => { if (!town && names.length) setTown(names.includes('Town03') ? 'Town03' : names[0]); }, [names, town]);
  const scene = useScene(town);
  const [lane, setLane] = useState<Lane | null>(null);
  const colour = (l: Lane) => (l.restricted ? '#fd7e14' : l.one_way ? '#15aabf' : l.ramp ? '#e64980' : l.road_class === 'highway' ? '#4c6ef5' : null);
  return (
    <Grid>
      <Grid.Col span={{ base: 12, md: 8 }}>
        <Group mb="xs">
          <Select data={names} value={town} onChange={setTown} w={200} />
          <Group gap={4}>
            {[['highway', '#4c6ef5'], ['ramp', '#e64980'], ['one-way', '#15aabf'], ['restricted', '#fd7e14'], ['bridge', '#7048e8']].map(([l, c]) => <Badge key={l} variant="dot" color={c}>{l}</Badge>)}
          </Group>
        </Group>
        <LaneMap lanes={scene.data?.lanes} zones={scene.data?.zones} laneColor={colour} selectedLane={lane?.id} onLaneClick={setLane} height="65vh" />
      </Grid.Col>
      <Grid.Col span={{ base: 12, md: 4 }}>
        <Card withBorder>
          <Text fw={600} mb="xs">{lane ? `Lane ${lane.id}` : 'Click a lane'}</Text>
          {lane && (
            <Table withRowBorders={false} verticalSpacing={2}>
              <Table.Tbody>
                {Object.entries(lane).filter(([k]) => !['centreline', 'z'].includes(k)).map(([k, v]) => (
                  <Table.Tr key={k}><Table.Td><Text size="xs" c="dimmed">{k}</Text></Table.Td><Table.Td><Text size="xs">{Array.isArray(v) ? v.join(', ') : String(v)}</Text></Table.Td></Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
          <Text size="xs" c="dimmed" mt="sm">Road attributes come from the lane map (export_lane_map.py); site overrides via zone_tool.py or the 3D twin.</Text>
        </Card>
      </Grid.Col>
    </Grid>
  );
}

export default function ConfigPage() {
  const list = useQuery({ queryKey: ['profiles'], queryFn: api.profiles });
  const names = (list.data ?? []).map(profileName);
  const [name, setName] = useState<string | null>(null);
  useEffect(() => { if (!name && names.length) setName(names.includes('default') ? 'default' : names[0]); }, [names, name]);
  return (
    <Stack>
      <Title order={3}>Configuration</Title>
      <Tabs defaultValue="profiles">
        <Tabs.List>
          <Tabs.Tab value="profiles">Profiles & thresholds</Tabs.Tab>
          <Tabs.Tab value="road">Road configuration</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="profiles" pt="md">
          <Group mb="md"><Select label="Profile" data={names} value={name} onChange={setName} w={240} /></Group>
          {name && <ProfileEditor key={name} name={name} />}
        </Tabs.Panel>
        <Tabs.Panel value="road" pt="md"><RoadView /></Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
