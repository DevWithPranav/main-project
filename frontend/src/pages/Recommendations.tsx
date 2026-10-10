import { Alert, Badge, Button, Card, Code, Group, Modal, SegmentedControl, SimpleGrid, Stack, Table, Text, Textarea, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api';
import type { Page, PlannerHistoryEntry, Recommendation } from '../api/types';
import { useAuth } from '../auth';
import { fmtDateTime, fmtValue } from '../lib/format';
import { can } from '../lib/permissions';

const list = <T,>(x: T[] | Page<T> | undefined): T[] => (Array.isArray(x) ? x : (x?.items ?? []));
/** recommend.py gives action as {countermeasure, summary, catalogue_name}; older shapes are plain text. */
const actionText = (a: unknown): string =>
  a && typeof a === 'object' && 'summary' in a ? String((a as { summary: unknown }).summary) : text(a);
const text = (v: unknown): string =>
  v == null ? '-' : typeof v === 'string' ? v : Array.isArray(v) ? v.map(text).join('; ') : typeof v === 'object' ? Object.entries(v as object).map(([k, x]) => `${k}: ${fmtValue(x)}`).join(', ') : String(v);

function RecCard({ r, onDecide, canDecide }: { r: Recommendation; onDecide: (r: Recommendation) => void; canDecide: boolean }) {
  const fields: [string, unknown][] = [
    ['Location', r.location], ['Evidence', r.evidence], ['Expected impact', r.expected_impact ?? r.projected_estimate],
    ['Validation', r.validation_method], ['Confidence', r.confidence], ['Limitations', r.limitations], ['Alternatives', r.alternatives],
  ];
  return (
    <Card withBorder>
      <Group justify="space-between" mb={6}>
        <Group gap={6}>
          {r.type && <Badge>{String(r.type).replace(/_/g, ' ')}</Badge>}
          {r.priority != null && <Badge color="orange" variant="light">priority {text(r.priority)}</Badge>}
          {(r.decision || r.status) && <Badge color="gray" variant="outline">{r.decision ?? r.status}</Badge>}
        </Group>
        <Code>{r.id}</Code>
      </Group>
      <Text fw={600}>{typeof r.problem === 'string' ? r.problem : text(r.problem)}</Text>
      <Text mt={4}>→ {actionText(r.action)}</Text>
      <Table mt="xs" withRowBorders={false} verticalSpacing={2}>
        <Table.Tbody>
          {fields.map(([k, v]) => (
            <Table.Tr key={k}><Table.Td w={130}><Text size="xs" c="dimmed">{k}</Text></Table.Td><Table.Td><Text size="xs">{text(v)}</Text></Table.Td></Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {canDecide && <Group mt="sm"><Button size="xs" onClick={() => onDecide(r)}>Decide…</Button></Group>}
    </Card>
  );
}

export default function RecommendationsPage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const recs = useQuery({ queryKey: ['recs'], queryFn: api.recommendations });
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
  const items = list(recs.data);
  const history = list<PlannerHistoryEntry>(hist.data);

  return (
    <Stack>
      <Title order={3}>Recommendations</Title>
      <Text size="sm" c="dimmed">From hotspots and patterns in the event data. Expected impacts are projected estimates until a baseline-vs-modified CARLA run validates them.</Text>
      {recs.isError && <Alert color="red">{(recs.error as Error).message}</Alert>}
      {!recs.isLoading && items.length === 0 && <Alert color="gray">No recommendations yet: they need imported sessions with enough events.</Alert>}
      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        {items.map((r) => <RecCard key={r.id} r={r} onDecide={setCur} canDecide={can(me?.role, 'decide_recommendation')} />)}
      </SimpleGrid>
      <Card withBorder>
        <Text fw={600} mb="xs">Planner history</Text>
        {history.length === 0 ? <Text size="sm" c="dimmed">No decisions yet.</Text> : (
          <Table>
            <Table.Thead><Table.Tr><Table.Th>When</Table.Th><Table.Th>Recommendation</Table.Th><Table.Th>Decision</Table.Th><Table.Th>By</Table.Th><Table.Th>Rationale</Table.Th><Table.Th>Validation</Table.Th></Table.Tr></Table.Thead>
            <Table.Tbody>
              {history.map((h, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{fmtDateTime(h.at)}</Table.Td><Table.Td><Code>{h.recommendation_id ?? '-'}</Code></Table.Td>
                  <Table.Td><Badge variant="light">{h.decision}</Badge></Table.Td><Table.Td>{h.by ?? '-'}</Table.Td>
                  <Table.Td>{h.rationale ?? '-'}</Table.Td><Table.Td><Text size="xs">{text(h.validation)}</Text></Table.Td>
                </Table.Tr>
              ))}
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

