import { Alert, Badge, Card, Group, Loader, Pagination, Stack, Table, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api';
import { eventTime, isAnomaly, type TrafficEvent } from '../api/types';
import EventDrawer from '../components/EventDrawer';
import FilterBar, { useFilters } from '../components/Filters';
import { useConditions } from '../hooks';
import { conditionLabel, fmtSimTime, reviewState, STATUS_COLORS, statusLabel, TYPE_COLORS, typeLabel } from '../lib/format';

const PAGE = 50;
const REVIEW_COLORS = { none: 'gray', confirmed: 'green', dismissed: 'dark' } as const;

export default function ViolationsPage() {
  const [filters, setFilters] = useFilters();
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<TrafficEvent | null>(null);
  const { idx } = useConditions();
  const q = useQuery({
    queryKey: ['events', filters, page],
    queryFn: () => api.events(filters, { limit: PAGE, offset: (page - 1) * PAGE }),
    placeholderData: (prev) => prev,
  });
  const total = q.data?.total ?? 0;

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Violations & anomalies</Title>
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
                <Table.Th>Session</Table.Th><Table.Th>Status</Table.Th><Table.Th>Review</Table.Th><Table.Th>Conf.</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {q.isLoading && (
                <Table.Tr><Table.Td colSpan={8}><Loader size="sm" m="md" /></Table.Td></Table.Tr>
              )}
              {!q.isLoading && !q.data?.items.length && (
                <Table.Tr><Table.Td colSpan={8}><Text c="dimmed" p="md">No events match. Import a processed flight under Sessions.</Text></Table.Td></Table.Tr>
              )}
              {q.data?.items.map((e) => (
                <Table.Tr key={e.event_id} style={{ cursor: 'pointer' }} onClick={() => setSelected(e)}>
                  <Table.Td><Badge color={TYPE_COLORS[e.type] ?? 'gray'} size="sm">{typeLabel(e.type)}</Badge></Table.Td>
                  <Table.Td><Text size="sm">{isAnomaly(e) ? '-' : conditionLabel(e.condition, idx)}</Text></Table.Td>
                  <Table.Td><Text size="sm">{isAnomaly(e) ? `severity ${e.severity_score.toFixed(2)}` : `${e.cls} #${e.track_ids.join(', #')}`}</Text></Table.Td>
                  <Table.Td><Text size="sm">{fmtSimTime(eventTime(e))}</Text></Table.Td>
                  <Table.Td><Text size="sm">{e.session_id ?? '-'}</Text></Table.Td>
                  <Table.Td><Badge variant="light" color={STATUS_COLORS[e.status] ?? 'gray'} size="sm">{statusLabel(e.status)}</Badge></Table.Td>
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
