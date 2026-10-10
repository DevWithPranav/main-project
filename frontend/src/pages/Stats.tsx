import { BarChart, LineChart } from '@mantine/charts';
import { Alert, Card, Grid, Group, Loader, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { api } from '../api';
import FilterBar, { useFilters } from '../components/Filters';
import LaneMap from '../components/LaneMap';
import { sessionTown, useConditions, useScene, useSessions } from '../hooks';
import { conditionLabel, statusLabel, typeLabel } from '../lib/format';
import { binPoints, sortDesc, toBuckets, toHotspots, toHourly } from '../lib/stats';

function Tile({ label, value }: { label: string; value: string | number }) {
  return (
    <Card withBorder p="sm">
      <Text size="xs" c="dimmed" tt="uppercase">{label}</Text>
      <Text fw={700} size="xl">{value}</Text>
    </Card>
  );
}

export default function StatsPage() {
  const [filters, setFilters] = useFilters();
  const { idx } = useConditions();
  const stats = useQuery({ queryKey: ['stats', filters], queryFn: () => api.stats(filters) });
  const points = useQuery({ queryKey: ['events', filters, 'map'], queryFn: () => api.events(filters, { limit: 5000 }) });
  const sessions = useSessions();
  const town = sessionTown(sessions.data?.find((s) => s.session_id === filters.session_id) ?? sessions.data?.[0]);
  const scene = useScene(town);

  const byType = sortDesc(toBuckets(stats.data?.by_type)).map((b) => ({ name: typeLabel(b.key), count: b.count }));
  const byCond = sortDesc(toBuckets(stats.data?.by_condition)).map((b) => ({ name: b.key, label: conditionLabel(b.key, idx), count: b.count }));
  const byStatus = toBuckets(stats.data?.by_status).map((b) => ({ name: statusLabel(b.key), count: b.count }));
  const hourly = toHourly(stats.data?.by_hour).map((b) => ({ hour: b.key, count: b.count }));
  const hotspots = toHotspots(stats.data?.hotspots);
  const total = byType.reduce((s, b) => s + b.count, 0);
  const sameTown = !!filters.session_id || (sessions.data?.length ?? 0) <= 1;
  const heat = useMemo(
    () => binPoints(points.data?.items ?? [], 25).map((c) => ({ x: c.x, y: c.y, size: 25, n: c.n })),
    [points.data],
  );

  return (
    <Stack>
      <Title order={3}>Statistics</Title>
      <FilterBar value={filters} onChange={setFilters} />
      {stats.isError && <Alert color="red">{(stats.error as Error).message}</Alert>}
      {stats.isLoading ? <Loader /> : (
        <>
          <SimpleGrid cols={{ base: 2, md: 4 }}>
            <Tile label="Events" value={total} />
            <Tile label="Types" value={byType.length} />
            <Tile label="Conditions" value={byCond.length} />
            <Tile label="Hotspots" value={hotspots.length} />
          </SimpleGrid>
          <Grid>
            <Grid.Col span={{ base: 12, md: 6 }}>
              <Card withBorder><Text fw={600} mb="sm">By type</Text>
                <BarChart h={260} data={byType} dataKey="name" series={[{ name: 'count', color: 'indigo.6' }]} yAxisProps={{ allowDecimals: false }} />
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 6 }}>
              <Card withBorder><Text fw={600} mb="sm">By condition</Text>
                <BarChart h={260} data={byCond} dataKey="name" series={[{ name: 'count', color: 'grape.6' }]} yAxisProps={{ allowDecimals: false }} />
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 6 }}>
              <Card withBorder><Text fw={600} mb="sm">By hour</Text>
                {hourly.length ? <LineChart h={240} data={hourly} dataKey="hour" series={[{ name: 'count', color: 'teal.6' }]} yAxisProps={{ allowDecimals: false }} curveType="monotone" />
                  : <Text c="dimmed" size="sm">No time data.</Text>}
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 6 }}>
              <Card withBorder><Text fw={600} mb="sm">By status</Text>
                <BarChart h={240} data={byStatus} dataKey="name" series={[{ name: 'count', color: 'orange.6' }]} yAxisProps={{ allowDecimals: false }} />
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 7 }}>
              <Card withBorder p="xs"><Text fw={600} mb="xs">Event density {town ? `(${town})` : ''}</Text>
                {sameTown ? <LaneMap lanes={scene.data?.lanes} heat={heat} height={380} rings={hotspots.slice(0, 10).map((h, i) => ({
                  id: i, x: h.x, y: h.y, r: h.radius_m ?? 12.5, label: `#${i + 1}`,
                  title: `#${i + 1}: ${h.count} events${h.by_type ? ` (${Object.entries(h.by_type).map(([k, n]) => `${typeLabel(k)} ${n}`).join(', ')})` : ''}`,
                }))} />
                  : <Text c="dimmed" size="sm">Pick one session to see its map (sessions may be different towns).</Text>}
              </Card>
            </Grid.Col>
            <Grid.Col span={{ base: 12, md: 5 }}>
              <Card withBorder><Text fw={600} mb="sm">Hotspot ranking</Text>
                {hotspots.length === 0 ? <Text c="dimmed" size="sm">No hotspots (needs enough events in one place).</Text> : (
                  <Table>
                    <Table.Thead><Table.Tr><Table.Th>#</Table.Th><Table.Th>Where</Table.Th><Table.Th>Types</Table.Th><Table.Th>Events</Table.Th></Table.Tr></Table.Thead>
                    <Table.Tbody>
                      {hotspots.slice(0, 10).map((h, i) => (
                        <Table.Tr key={i}><Table.Td>{i + 1}</Table.Td><Table.Td><Text size="sm">{h.label}</Text><Text size="xs" c="dimmed">x {h.x.toFixed(0)}, y {h.y.toFixed(0)} m</Text></Table.Td><Table.Td>{h.by_type ? Object.entries(h.by_type).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${typeLabel(k)} ${n}`).join(', ') : h.type ? typeLabel(h.type) : 'all'}</Table.Td><Table.Td>{h.count}</Table.Td></Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                )}
              </Card>
            </Grid.Col>
          </Grid>
          <Group><Text size="xs" c="dimmed">Charts follow the filters above; exports use the same filters.</Text></Group>
        </>
      )}
    </Stack>
  );
}
