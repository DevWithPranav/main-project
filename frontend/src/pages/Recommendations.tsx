// Recommendations (Build Plan M8, Expected_Output 7.3): each proposal with its problem, action, where,
// evidence, projected impact, priority, validation plan, confidence, limitations and alternatives,
// rendered from the objects ml/planning/recommend.py returns (older plain-text shapes still show).

import {
  Accordion, Alert, Anchor, Badge, Button, Card, Code, Group, List, Modal, SegmentedControl, Select, SimpleGrid,
  Stack, Table, Text, Textarea, Title, Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { api, TWIN_URL } from '../api';
import type { Page, PlannerHistoryEntry, Recommendation } from '../api/types';
import { useAuth } from '../auth';
import { fmtDateTime, fmtValue } from '../lib/format';
import { can } from '../lib/permissions';

type Obj = Record<string, unknown>;
const list = <T,>(x: T[] | Page<T> | undefined): T[] => (Array.isArray(x) ? x : (x?.items ?? []));
const obj = (v: unknown): Obj => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Obj) : {});
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : v == null ? [] : [v]);
const str = (v: unknown): string => (v == null ? '' : typeof v === 'string' ? v : typeof v === 'number' ? fmtValue(v) : '');
/** Plain-text fallback for shapes this page doesn't know. */
const text = (v: unknown): string =>
  v == null ? '-' : typeof v === 'string' ? v : Array.isArray(v) ? v.map(text).join('; ')
    : typeof v === 'object' ? Object.entries(v as object).map(([k, x]) => `${k}: ${fmtValue(x)}`).join(', ') : String(v);
const label = (s: string) => s.replace(/_/g, ' ');
const BAND_COLOR: Record<string, string> = { high: 'red', medium: 'orange', low: 'gray' };
const SIM_COLOR: Record<string, string> = { yes: 'green', partly: 'yellow', no: 'gray' };

function actionText(a: unknown): string {
  const o = obj(a);
  return str(o.summary) || str(o.catalogue_name) || text(a);
}

function Row({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <Table.Tr>
      <Table.Td w={150} style={{ verticalAlign: 'top' }}><Text size="xs" c="dimmed">{k}</Text></Table.Td>
      <Table.Td><Text size="sm" component="div">{children}</Text></Table.Td>
    </Table.Tr>
  );
}

function Counts({ m }: { m: unknown }) {
  const e = Object.entries(obj(m)).sort((a, b) => Number(b[1]) - Number(a[1]));
  if (!e.length) return null;
  return <Group gap={4}>{e.map(([k, n]) => <Badge key={k} size="sm" variant="light" color="gray">{label(k)} {String(n)}</Badge>)}</Group>;
}

function twinLink(r: Recommendation, sessionId: string | null): string | null {
  const town = str(obj(r.location).scene) || str(obj(r.simulation).town);
  if (!town) return null;
  const q = new URLSearchParams({ town });
  if (sessionId) q.set('session', sessionId);
  return `${TWIN_URL}/?${q.toString()}`;
}

function RecCard({ r, sessionId, onDecide, canDecide }: {
  r: Recommendation; sessionId: string | null; onDecide: (r: Recommendation) => void; canDecide: boolean;
}) {
  const pr = obj(r.priority), conf = obj(r.confidence), loc = obj(r.location), ev = obj(r.evidence);
  const imp = obj(r.expected_impact ?? r.projected_estimate), val = obj(r.validation_method), sim = obj(r.simulation);
  const before = obj(imp.before), src = obj(imp.source), period = obj(ev.period), scen = obj(val.scenario);
  const band = str(pr.band), simOk = str(sim.supported);
  const twin = twinLink(r, sessionId);
  const roads = arr(loc.road_ids).map(String);
  const known = Object.keys(pr).length > 0;  // the structured M8 shape
  return (
    <Card withBorder>
      <Group justify="space-between" mb={6} wrap="nowrap" align="flex-start">
        <Group gap={6}>
          {r.type && <Badge>{label(String(r.type))}</Badge>}
          {r.area && <Badge variant="outline" color="gray">{r.area}</Badge>}
          {known ? (
            <Tooltip label={str(pr.explanation) || 'priority'} multiline w={360}>
              <Badge color={BAND_COLOR[band] ?? 'gray'}>priority {band} · {str(pr.score)}</Badge>
            </Tooltip>
          ) : r.priority != null && <Badge color="orange" variant="light">priority {text(r.priority)}</Badge>}
          {r.tier && <Badge variant="light" color="grape">{label(r.tier)}</Badge>}
          {(r.decision || r.status) && <Badge color="gray" variant="outline">{r.decision ?? r.status}</Badge>}
        </Group>
        <Code>{r.id}</Code>
      </Group>
      <Text fw={600}>{typeof r.problem === 'string' ? r.problem : text(r.problem)}</Text>
      <Text mt={4}>→ {actionText(r.action)}</Text>
      {!known ? (
        <Table mt="xs" withRowBorders={false} verticalSpacing={2}>
          <Table.Tbody>
            {([['Location', r.location], ['Evidence', r.evidence], ['Expected impact', r.expected_impact ?? r.projected_estimate],
              ['Validation', r.validation_method], ['Confidence', r.confidence], ['Limitations', r.limitations],
              ['Alternatives', r.alternatives]] as [string, unknown][]).map(([k, v]) => <Row key={k} k={k}>{text(v)}</Row>)}
          </Table.Tbody>
        </Table>
      ) : (
        <>
          <Table mt="xs" withRowBorders={false} verticalSpacing={3}>
            <Table.Tbody>
              <Row k="Where">
                {[str(loc.scene) || str(sim.town), roads.length ? `road ${roads.join(', ')}` : ''].filter(Boolean).join(', ') || '-'}
                {loc.radius_m != null && ` · ${fmtValue(loc.radius_m)} m around (${arr(loc.centroid).map((x) => fmtValue(x)).join(', ')})`}
                {twin && <> · <Anchor href={twin} target="_blank" size="sm">open in twin</Anchor></>}
              </Row>
              <Row k="Evidence">
                <Stack gap={4}>
                  <Text size="sm">
                    {str(ev.count)} events over {fmtValue(Number(period.span_s ?? 0) / 60)} min
                    {ev.share_of_events_in_area != null && ` · ${Math.round(Number(ev.share_of_events_in_area) * 100)} % of the events in the area`}
                    {ev.mean_event_confidence != null && ` · mean confidence ${fmtValue(ev.mean_event_confidence)}`}
                  </Text>
                  <Counts m={ev.by_condition} />
                </Stack>
              </Row>
              <Row k="Expected impact">
                <Badge size="xs" color="yellow" variant="light" mr={6}>{str(imp.label) || 'projected estimate'}</Badge>
                {imp.reduction != null ? `${fmtValue(imp.reduction)} reduction` : str(imp.basis)}
                {before.per_hour != null && <Text size="xs" c="dimmed">now: {fmtValue(before.per_hour)} per hour ({str(before.rate_note)})</Text>}
                {src.name != null && (
                  <Text size="xs" c="dimmed">source: {src.url ? <Anchor href={str(src.url)} target="_blank" size="xs">{str(src.name)}</Anchor> : str(src.name)}</Text>
                )}
              </Row>
              <Row k="Confidence">
                <Badge size="sm" color={BAND_COLOR[str(conf.band)] ?? 'gray'} variant="light" mr={6}>{str(conf.band)} · {str(conf.score)}</Badge>
                <Text span size="xs" c="dimmed">{arr(conf.basis).map(String).join(' · ')}</Text>
              </Row>
              <Row k="CARLA validation">
                <Badge size="sm" color={SIM_COLOR[simOk] ?? 'gray'} variant="light" mr={6}>simulable: {simOk || '?'}</Badge>
                {str(obj(sim.change).reason) && <Text span size="xs" c="dimmed">{str(obj(sim.change).reason)}</Text>}
              </Row>
            </Table.Tbody>
          </Table>
          <Accordion variant="contained" mt="xs" chevronPosition="left">
            <Accordion.Item value="validation">
              <Accordion.Control><Text size="sm">How to validate</Text></Accordion.Control>
              <Accordion.Panel>
                <Text size="sm">{str(val.method)}</Text>
                {str(val.success_criterion) && <Text size="sm" mt={4}><b>Success:</b> {str(val.success_criterion)}</Text>}
                {arr(val.metrics).length > 0 && <List size="sm" mt={4}>{arr(val.metrics).map((m, i) => <List.Item key={i}>{String(m)}</List.Item>)}</List>}
                {Object.keys(scen).length > 0 && (
                  <Text size="xs" c="dimmed" mt={4}>
                    scenario: {str(scen.town)} · {str(scen.vehicles)} vehicles within {str(scen.radius_m)} m · {str(scen.duration_s)} s · seeds {arr(scen.seeds).join(', ')} · {str(scen.weather)}
                  </Text>
                )}
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="limits">
              <Accordion.Control><Text size="sm">Limitations ({arr(r.limitations).length})</Text></Accordion.Control>
              <Accordion.Panel><List size="sm">{arr(r.limitations).map((l, i) => <List.Item key={i}>{String(l)}</List.Item>)}</List></Accordion.Panel>
            </Accordion.Item>
            {arr(r.alternatives).length > 0 && (
              <Accordion.Item value="alts">
                <Accordion.Control><Text size="sm">Alternatives ({arr(r.alternatives).length})</Text></Accordion.Control>
                <Accordion.Panel>
                  <List size="sm">
                    {arr(r.alternatives).map((a, i) => {
                      const o = obj(a);
                      return <List.Item key={i}><b>{str(o.action) || text(a)}</b>{str(o.trade_off) && ` — ${str(o.trade_off)}`}</List.Item>;
                    })}
                  </List>
                </Accordion.Panel>
              </Accordion.Item>
            )}
          </Accordion>
        </>
      )}
      {canDecide && <Group mt="sm"><Button size="xs" onClick={() => onDecide(r)}>Decide…</Button></Group>}
    </Card>
  );
}

const ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

export default function RecommendationsPage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: api.sessions });
  const recs = useQuery({ queryKey: ['recs', sessionId], queryFn: () => api.recommendations(sessionId) });
  const hist = useQuery({ queryKey: ['plannerHistory'], queryFn: api.plannerHistory });
  const [cur, setCur] = useState<Recommendation | null>(null);
  const [decision, setDecision] = useState<'accepted' | 'rejected' | 'modified'>('accepted');
  const [why, setWhy] = useState('');
  const decide = useMutation({
    mutationFn: () => api.decide(cur!.id, decision, why),
    onSuccess: () => {
      notifications.show({ color: 'green', message: `Recommendation ${decision}` });
      setCur(null); setWhy('');
      qc.invalidateQueries({ queryKey: ['recs'] });
      qc.invalidateQueries({ queryKey: ['plannerHistory'] });
    },
    onError: (e: Error) => notifications.show({ color: 'red', title: 'Failed', message: e.message }),
  });
  const items = useMemo(() => [...list(recs.data)].sort((a, b) => {
    const pa = obj(a.priority), pb = obj(b.priority);
    return (ORDER[str(pa.band)] ?? 3) - (ORDER[str(pb.band)] ?? 3) || Number(pb.score ?? 0) - Number(pa.score ?? 0);
  }), [recs.data]);
  const history = list<PlannerHistoryEntry>(hist.data);
  const sessionOptions = (sessions.data ?? []).filter((s) => s.n_events > 0)
    .map((s) => ({ value: s.session_id, label: `${s.name ?? s.session_id} (${s.n_events} events)` }));

  return (
    <Stack>
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={3}>Recommendations</Title>
          <Text size="sm" c="dimmed">From hotspots and patterns in the event data. Expected impacts are projected estimates until a baseline-vs-modified CARLA run validates them.</Text>
        </div>
        <Select label="Session" placeholder="all sessions" clearable searchable w={340} data={sessionOptions}
          value={sessionId} onChange={setSessionId} />
      </Group>
      {recs.isError && <Alert color="red">{(recs.error as Error).message}</Alert>}
      {!recs.isLoading && items.length === 0 && <Alert color="gray">No recommendations yet: they need imported sessions with enough events.</Alert>}
      {items.length > 0 && <Text size="sm">{items.length} recommendations, highest priority first.</Text>}
      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        {items.map((r) => <RecCard key={r.id} r={r} sessionId={sessionId} onDecide={setCur} canDecide={can(me?.role, 'decide_recommendation')} />)}
      </SimpleGrid>
      <Card withBorder>
        <Text fw={600} mb="xs">Planner history</Text>
        {history.length === 0 ? <Text size="sm" c="dimmed">No decisions yet.</Text> : (
          <Table>
            <Table.Thead><Table.Tr><Table.Th>When</Table.Th><Table.Th>Recommendation</Table.Th><Table.Th>Decision</Table.Th><Table.Th>By</Table.Th><Table.Th>Rationale</Table.Th><Table.Th>Validation</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>
              {history.map((h, i) => {
                const rec = obj(h.recommendation);
                return (
                  <Table.Tr key={i}>
                    <Table.Td>{fmtDateTime(h.at)}</Table.Td>
                    <Table.Td>
                      <Text size="sm">{str(rec.problem) || '-'}</Text>
                      <Text size="xs" c="dimmed">{actionText(rec.action) !== '-' ? actionText(rec.action) : ''} <Code>{h.recommendation_id ?? '-'}</Code></Text>
                    </Table.Td>
                    <Table.Td><Badge variant="light">{h.decision}</Badge></Table.Td><Table.Td>{h.by ?? '-'}</Table.Td>
                    <Table.Td>{h.rationale ?? '-'}</Table.Td>
                    <Table.Td><Text size="xs">{h.validation == null ? 'not run yet' : text(h.validation)}</Text></Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        )}
      </Card>
      <Modal opened={!!cur} onClose={() => setCur(null)} title="Planner decision">
        <Stack>
          <Text size="sm">{actionText(cur?.action)}</Text>
          <SegmentedControl value={decision} onChange={(v) => setDecision(v as typeof decision)} data={['accepted', 'rejected', 'modified']} />
          <Textarea label="Rationale" required value={why} onChange={(e) => setWhy(e.currentTarget.value)} autosize minRows={3} />
          <Button onClick={() => decide.mutate()} loading={decide.isPending} disabled={!why.trim()}>Save decision</Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
