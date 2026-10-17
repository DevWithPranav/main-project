import { Alert, Badge, Card, Group, Loader, Pagination, Stack, Table, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api';
import { eventTime, isAnomaly, SHOWN_VIOLATION_TYPES, type TrafficEvent } from '../api/types';
import EventDrawer from '../components/EventDrawer';
import FilterBar, { useFilters } from '../components/Filters';
import { useConditions } from '../hooks';
import { conditionLabel, fmtDateTime, fmtSimTime, reviewState, STATUS_COLORS, statusLabel, TYPE_COLORS, typeLabel } from '../lib/format';

const PAGE = 50;
const REVIEW_COLORS = { none: 'gray', confirmed: 'green', dismissed: 'dark' } as const;
// docs/Live_Page_Changes.md: Type shown as bold text (the coloured badge is kept behind TYPE_AS_BADGE); Status column hidden
const TYPE_AS_BADGE = false;
const SHOW_STATUS_COLUMN = false;
const N_COLS = SHOW_STATUS_COLUMN ? 8 : 7;

export default function ViolationsPage() {
  const [filters, setFilters] = useFilters();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<TrafficEvent | null>(null);
  const { idx } = useConditions();
  const q = useQuery({
    queryKey: ['events', filters, page],
    // no type picked: ask for the shown types only, so the total and the pages leave the hidden ones out
    queryFn: () => api.events({ ...filters, type: filters.type ?? SHOWN_VIOLATION_TYPES.join(',') },
      { limit: PAGE, offset: (page - 1) * PAGE, order: 'desc' }),
    placeholderData: (prev) => prev,
    refetchInterval: 10_000,  // live sessions keep adding events (and their evidence)
  });
  const total = q.data?.total ?? 0;

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Violations</Title>
        <Text c="dimmed" size="sm">{total} events</Text>
      </Group>
      <FilterBar value={filters} onChange={(f) => { setPage(1); setFilters(f); }} />
      {q.isError && <Alert color="red">{(q.error as Error).message}</Alert>}
      <Card withBorder p={0}>
        <Table.ScrollContainer minWidth={900}>
          <Table highlightOnHover striped>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Type</Table.Th><Table.Th>Condition</Table.Th><Table.Th>Vehicle</Table.Th><Table.Th>Time</Table.Th>
                <Table.Th>Session</Table.Th>{SHOW_STATUS_COLUMN && <Table.Th>Status</Table.Th>}<Table.Th>Review</Table.Th><Table.Th>Conf.</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {q.isLoading && (
                <Table.Tr><Table.Td colSpan={N_COLS}><Loader size="sm" m="md" /></Table.Td></Table.Tr>
              )}
              {!q.isLoading && !q.data?.items.length && (
                <Table.Tr><Table.Td colSpan={N_COLS}><Text c="dimmed" p="md">No events match.</Text></Table.Td></Table.Tr>
              )}
              {q.data?.items.map((e) => (
                <Table.Tr key={e.event_id} style={{ cursor: 'pointer' }} onClick={() => setSelected(e)}>
                  <Table.Td>{TYPE_AS_BADGE
                    ? <Badge color={TYPE_COLORS[e.type] ?? 'gray'} size="sm">{typeLabel(e.type)}</Badge>
                    : <Text size="sm" fw={700}>{typeLabel(e.type)}</Text>}</Table.Td>
                  <Table.Td><Text size="sm">{isAnomaly(e) ? '-' : conditionLabel(e.condition, idx)}</Text></Table.Td>
                  <Table.Td><Text size="sm">{isAnomaly(e) ? `severity ${e.severity_score.toFixed(2)}` : `${e.cls} #${e.track_ids.join(', #')}`}</Text></Table.Td>
                  <Table.Td>
                    <Text size="sm">{fmtDateTime((e as TrafficEvent & { occurred_at?: string }).occurred_at)}</Text>
                    <Text size="xs" c="dimmed">sim {fmtSimTime(eventTime(e))}</Text>
                  </Table.Td>
                  <Table.Td><Text size="sm">{e.session_id ?? '-'}</Text></Table.Td>
                  {SHOW_STATUS_COLUMN && <Table.Td><Badge variant="light" color={STATUS_COLORS[e.status] ?? 'gray'} size="sm">{statusLabel(e.status)}</Badge></Table.Td>}
                  <Table.Td><Badge variant="outline" color={REVIEW_COLORS[reviewState(e)]} size="sm">{reviewState(e)}</Badge></Table.Td>
                  <Table.Td><Text size="sm">{e.confidence.toFixed(2)}</Text></Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Card>
      {total > PAGE && <Pagination total={Math.ceil(total / PAGE)} value={page} onChange={setPage} />}
      <EventDrawer event={selected} onClose={() => setSelected(null)} />
    </Stack>
  );
}
