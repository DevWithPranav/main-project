// Configuration: profiles (violation on/off + thresholds, conditions, modules), validated against
// schemas/profile.schema.json before saving a new version with a note; version history; road configuration editor (components/RoadEditor.tsx).

import { Accordion, Alert, Badge, Button, Card, Code, Grid, Group, JsonInput, NumberInput, Select, SimpleGrid, Stack, Switch, Tabs, Text, Textarea, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { Profile, ViolationType } from '../api/types';
import { useAuth } from '../auth';
import RoadEditor from '../components/RoadEditor';
import ZoneDrawer from '../components/ZoneDrawer';
import { useConditions } from '../hooks';
import { fmtDateTime, typeLabel } from '../lib/format';
import { can } from '../lib/permissions';
import { cloneProfile, DEFAULT_PROFILE, diffPaths, paramLabel, profileName, unwrapProfile, validateProfile } from '../lib/profiles';

function ParamInput({ k, v, onChange, disabled, label }: { k: string; v: unknown; onChange: (v: unknown) => void; disabled: boolean; label?: string }) {
  if (typeof v === 'boolean') return <Switch label={label ?? paramLabel(k)} checked={v} onChange={(e) => onChange(e.currentTarget.checked)} disabled={disabled} />;
  if (typeof v === 'number')
    return <NumberInput label={label ?? paramLabel(k)} value={v} onChange={(x) => onChange(typeof x === 'number' ? x : Number(x))} decimalScale={4} disabled={disabled} size="xs" />;
  return (
    <JsonInput label={label ?? paramLabel(k)} value={JSON.stringify(v, null, 1)} size="xs" autosize maxRows={6} disabled={disabled}
      onChange={(s) => { try { onChange(JSON.parse(s)); } catch { /* keep editing */ } }} />
  );
}

// hidden, not removed (docs/Live_Page_Changes.md): the Profile selector (the default profile is edited),
// the profile's description line and the version History card
const SHOW_PROFILE_SELECT = true;
const SHOW_PROFILE_DESCRIPTION = false;
const SHOW_PROFILE_HISTORY = false;
const SHOW_SAVE_CARD = true; // change summary, note box, Save new version / Reset
// violation types with a threshold card: the four used now; the others stay in the profile, just not listed (null = all)
// per type, the one setting its card shows (others stay in the profile, not shown); a type left out shows all
const SHOWN_PARAMS: Partial<Record<ViolationType, Record<string, string>>> = {
  speeding: { max_speed_kmh: "Max speed (km/h), 0 = the map's lane limits" },
  no_parking: { min_s: 'Max stop time (s)' },
  zebra_crossing: { min_s: 'Max stop time on the crossing (s)' },
  highway_stop: { min_s: 'Max stop time (s)' },
};
const SHOW_CONDITIONS = false; // "Conditions on/off" section
const SHOW_MODEL_SETTINGS = false; // "Model, modules and road settings" section
const SHOWN_TYPES: ViolationType[] | null = ['speeding', 'no_parking', 'highway_stop', 'zebra_crossing'];

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
  const types = (Object.keys({ ...DEFAULT_PROFILE.violations, ...viol }) as ViolationType[]).filter((t) => !SHOWN_TYPES || SHOWN_TYPES.includes(t));
  const setViol = (t: ViolationType, patch: object) =>
    setDraft({ ...draft, violations: { ...viol, [t]: { ...(viol[t] ?? {}), ...patch } } });

  return (
    <Grid>
      <Grid.Col span={{ base: 12, lg: SHOW_PROFILE_HISTORY ? 8 : 12 }}>
        <Stack>
          {SHOW_PROFILE_DESCRIPTION && draft.description && <Text size="sm" c="dimmed">{draft.description}</Text>}
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
                      {Object.entries(params).filter(([k]) => !SHOWN_PARAMS[t] || k in SHOWN_PARAMS[t]!).map(([k, v]) => (
                        <ParamInput key={k} k={k} v={v} label={SHOWN_PARAMS[t]?.[k]} disabled={!editable} onChange={(nv) => setViol(t, { params: { ...params, [k]: nv } })} />
                      ))}
                    </SimpleGrid>
                  </Accordion.Panel>
                </Accordion.Item>
              );
            })}
            {SHOW_CONDITIONS && <Accordion.Item value="conditions">
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
            </Accordion.Item>}
            {SHOW_MODEL_SETTINGS && <Accordion.Item value="model">
              <Accordion.Control><Text fw={500}>Model, modules and road settings</Text></Accordion.Control>
              <Accordion.Panel>
                {(['model', 'modules', 'road', 'place_memory', 'applies_to'] as const).map((k) => (
                  <JsonInput key={k} label={k} mb="xs" autosize maxRows={10} disabled={!editable} value={JSON.stringify(draft[k] ?? {}, null, 1)}
                    onChange={(s) => { try { setDraft({ ...draft, [k]: JSON.parse(s) }); } catch { /* keep editing */ } }} />
                ))}
              </Accordion.Panel>
            </Accordion.Item>}
          </Accordion>
          {SHOW_SAVE_CARD && editable && (
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
      <Grid.Col span={{ base: 12, lg: 4 }} hidden={!SHOW_PROFILE_HISTORY}>
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

const SHOW_ROAD_TAB = false; // "Road configuration" tab hidden, not removed (docs/Live_Page_Changes.md)

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
          <Tabs.Tab value="profiles">Thresholds</Tabs.Tab>
          {SHOW_ROAD_TAB && <Tabs.Tab value="road">Road configuration</Tabs.Tab>}
          <Tabs.Tab value="zones">Zones</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="profiles" pt="md">
          {SHOW_PROFILE_SELECT && <Group mb="md"><Select label="Profile" data={names} value={name} onChange={setName} w={240} /></Group>}
          {name && <ProfileEditor key={name} name={name} />}
        </Tabs.Panel>
        {SHOW_ROAD_TAB && <Tabs.Panel value="road" pt="md"><RoadEditor /></Tabs.Panel>}
        <Tabs.Panel value="zones" pt="md"><ZoneDrawer /></Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
