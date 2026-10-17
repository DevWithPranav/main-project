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
import { Link } from 'react-router-dom';
import { api } from '../api';
import type { Page, PlannerHistoryEntry, Recommendation } from '../api/types';
import { useAuth } from '../auth';
import { useConditions } from '../hooks';
import { fmtDateTime, fmtValue, typeLabel } from '../lib/format';
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
const SIM_COLOR: Record<string, string> = { yes: 'green', partial: 'yellow', partly: 'yellow', rule_side: 'yellow', no: 'gray' };
// plain-English wording (docs/Live_Page_Changes.md); the Decide button and the recommendation id are hidden
const SHOW_DECIDE = false;
const SHOW_REC_ID = false;
// short card: count + place + solution only; the rest (where / evidence / impact / confidence / test plan /
// limitations / other options) is kept behind this switch
const SHOW_DETAILS = false;
// recommendation types not listed (docs/Live_Page_Changes.md); the backend still makes them
const HIDDEN_REC_TYPES = new Set(['wrong_way', 'lane_violation']);
const BAND_PLAIN: Record<string, string> = { high: 'High', medium: 'Medium', low: 'Low' };
const TIER_PLAIN: Record<string, string> = { major_hotspot: 'Major hotspot', hotspot: 'Hotspot', emerging: 'Early pattern', flow: 'Traffic queue' };
const SIM_PLAIN: Record<string, string> = { yes: 'Yes', partial: 'Partly', partly: 'Partly', rule_side: 'Only the rules', no: 'No' };
/** "speeding violations", "lane violations" (not "lane violation violations"), singular for 1 */
const countNoun = (type: string, n: number) => {
  const t = (type ? typeLabel(type) : 'violation').toLowerCase().replace(/ violations?$/, '');
  return `${t} violation${n === 1 ? '' : 's'}`;
};
const pct = (v: unknown) => `${Math.round(Number(v) * 100)}%`;

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
  const { idx } = useConditions();
  const e = Object.entries(obj(m)).sort((a, b) => Number(b[1]) - Number(a[1]));
  if (!e.length) return null;
  // condition names, not their codes ("Stopping on the highway: 123", not "B1 123")
  return <Group gap={4}>{e.map(([k, n]) => <Badge key={k} size="sm" variant="light" color="gray" tt="none">{idx.get(k)?.name ?? label(k)}: {String(n)}</Badge>)}</Group>;
}

function twinLink(r: Recommendation, sessionId: string | null): string | null {
  const town = str(obj(r.location).scene) || str(obj(r.simulation).town);
  if (!town) return null;
  const q = new URLSearchParams({ town });
  if (sessionId) q.set('session', sessionId);
  return `/twin?${q.toString()}`;
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
          {r.type && <Badge tt="none">{typeLabel(String(r.type))}</Badge>}
          {known ? (
            <Tooltip label={str(pr.explanation) || 'priority'} multiline w={360}>
              <Badge color={BAND_COLOR[band] ?? 'gray'} tt="none">Priority: {BAND_PLAIN[band] ?? band}</Badge>
            </Tooltip>
          ) : r.priority != null && <Badge color="orange" variant="light" tt="none">Priority: {text(r.priority)}</Badge>}
          {SHOW_DETAILS && r.tier && <Badge variant="light" color="grape" tt="none">{TIER_PLAIN[r.tier] ?? label(r.tier)}</Badge>}
          {r.decision && <Badge color="gray" variant="outline" tt="none">{r.decision}</Badge>}
        </Group>
        {SHOW_REC_ID && <Code>{r.id}</Code>}
      </Group>
      {SHOW_DETAILS ? (
        <>
          <Text fw={600}>{typeof r.problem === 'string' ? r.problem : text(r.problem)}</Text>
          <Text mt={4}><Text span fw={600}>What to do: </Text>{actionText(r.action)}</Text>
        </>
      ) : (
        <>
          {/* the short card: how many violations where, and the fix */}
          <Text fw={600}>
            {str(ev.count) || '?'} {countNoun(String(r.type ?? ''), Number(ev.count))} recorded
            {roads.length ? ` on road ${roads.join(', ')}` : ''}{str(loc.scene) ? ` (${str(loc.scene)})` : ''}
            {twin && <> · <Anchor component={Link} to={twin} size="sm" fw={400}>show on the 3D map</Anchor></>}
          </Text>
          <Text mt={4}><Text span fw={600}>Solution: </Text>{actionText(r.action)}</Text>
          <Text size="xs" c="dimmed" mt={6}>For more detail, test this change in the CARLA simulator.</Text>
        </>
      )}
      {!SHOW_DETAILS ? null : !known ? (
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
                {loc.radius_m != null && `, within about ${Math.round(Number(loc.radius_m))} m of one spot`}
                {twin && <> · <Anchor component={Link} to={twin} size="sm">show on the 3D map</Anchor></>}
              </Row>
              <Row k="What we saw">
                <Stack gap={4}>
                  <Text size="sm">
                    {str(ev.count)} times in {Math.round(Number(period.span_s ?? 0) / 60)} minutes
                    {ev.share_of_events_in_area != null && `, ${pct(ev.share_of_events_in_area)} of all problems in this area`}
                    {ev.mean_event_confidence != null && `. The system was ${pct(ev.mean_event_confidence)} sure on average`}
                  </Text>
                  <Counts m={ev.by_condition} />
                </Stack>
              </Row>
              <Row k="How much it could help">
                {imp.reduction != null
                  ? `About ${pct(obj(imp.reduction).low)} to ${pct(obj(imp.reduction).high)} fewer (${str(imp.basis)})`
                  : str(imp.basis)}
                {before.per_hour != null && <Text size="xs" c="dimmed">Right now: about {Math.round(Number(before.per_hour))} times an hour ({str(before.rate_note)})</Text>}
                {imp.reduction != null && src.name != null && (
                  <Text size="xs" c="dimmed">Source: {src.url ? <Anchor href={str(src.url)} target="_blank" size="xs">{str(src.name)}</Anchor> : str(src.name)}</Text>
                )}
              </Row>
              <Row k="How sure we are">
                <Badge size="sm" color={BAND_COLOR[str(conf.band)] ?? 'gray'} variant="light" mr={6} tt="none">{BAND_PLAIN[str(conf.band)] ?? str(conf.band)} ({pct(conf.score)})</Badge>
                <Text span size="xs" c="dimmed">{arr(conf.basis).map(String).join(', ')}</Text>
              </Row>
              <Row k="Can we test it in the simulator?">
                <Badge size="sm" color={SIM_COLOR[simOk] ?? 'gray'} variant="light" mr={6} tt="none">{SIM_PLAIN[simOk] ?? (simOk || '?')}</Badge>
                {str(obj(sim.change).reason) && <Text span size="xs" c="dimmed">{str(obj(sim.change).reason)}</Text>}
              </Row>
            </Table.Tbody>
          </Table>
          <Accordion variant="contained" mt="xs" chevronPosition="left">
            <Accordion.Item value="validation">
              <Accordion.Control><Text size="sm">How to test it</Text></Accordion.Control>
              <Accordion.Panel>
                <Text size="sm">{str(val.method)}.</Text>
                {str(val.success_criterion) && <Text size="sm" mt={4}>{str(val.success_criterion)}.</Text>}
                {arr(val.metrics).length > 0 && (
                  <>
                    <Text size="sm" mt={4}>What we measure:</Text>
                    <List size="sm">{arr(val.metrics).map((m, i) => <List.Item key={i}>{String(m)}</List.Item>)}</List>
                  </>
                )}
                {Object.keys(scen).length > 0 && (
                  <Text size="xs" c="dimmed" mt={4}>
                    Test setup: {str(scen.town)}, {str(scen.vehicles)} cars within {str(scen.radius_m)} m, {Math.round(Number(scen.duration_s ?? 0) / 60)} minutes, {arr(scen.seeds).length} runs
                  </Text>
                )}
              </Accordion.Panel>
            </Accordion.Item>
            <Accordion.Item value="limits">
              <Accordion.Control><Text size="sm">Things to keep in mind ({arr(r.limitations).length})</Text></Accordion.Control>
              <Accordion.Panel><List size="sm">{arr(r.limitations).map((l, i) => <List.Item key={i}>{String(l)}</List.Item>)}</List></Accordion.Panel>
            </Accordion.Item>
            {arr(r.alternatives).length > 0 && (
              <Accordion.Item value="alts">
                <Accordion.Control><Text size="sm">Other options ({arr(r.alternatives).length})</Text></Accordion.Control>
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
      {SHOW_DECIDE && canDecide && <Group mt="sm"><Button size="xs" onClick={() => onDecide(r)}>Decide…</Button></Group>}
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
  const items = useMemo(() => [...list(recs.data)].filter((r) => !HIDDEN_REC_TYPES.has(String(r.type))).sort((a, b) => {
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
          <Text size="sm" c="dimmed">Suggestions based on where and how often rules are broken. The expected effects are rough guides until they are tested in the simulator.</Text>
        </div>
        <Select label="Session" placeholder="all sessions" clearable searchable w={340} data={sessionOptions}
          value={sessionId} onChange={setSessionId} />
      </Group>
      {recs.isError && <Alert color="red">{(recs.error as Error).message}</Alert>}
      {!recs.isLoading && items.length === 0 && <Alert color="gray">No suggestions yet: there are not enough recorded problems.</Alert>}
      {items.length > 0 && <Text size="sm">{items.length} suggestions, most important first.</Text>}
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
