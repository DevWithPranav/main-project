import { Alert, Badge, Button, Card, Group, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { useSessions } from '../hooks';
import { fmtDateTime } from '../lib/format';
import { can } from '../lib/permissions';

export default function SessionsPage() {
  const { me } = useAuth();
  const sessions = useSessions();
  const qc = useQueryClient();
  const nav = useNavigate();
  const [flight, setFlight] = useState('');
  const [vdir, setVdir] = useState('');
  const imp = useMutation({
    mutationFn: () => api.importSession({ flight: flight.trim(), violations_dir: vdir.trim() || undefined }),
    onSuccess: (s) => {
      notifications.show({ color: 'green', title: 'Imported', message: `${s.session_id}: ${s.n_events} events` });
      qc.invalidateQueries({ queryKey: ['sessions'] });
      qc.invalidateQueries({ queryKey: ['events'] });
      setFlight('');
    },
    onError: (e: Error) => notifications.show({ color: 'red', title: 'Import failed', message: e.message }),
  });

  return (
    <Stack>
      <Title order={3}>Sessions</Title>
      {can(me?.role, 'import_session') && (
        <Card withBorder>
          <Text fw={600} mb="xs">Import a processed flight</Text>
          <Group align="end">
            <TextInput label="Flight" placeholder="20261010_120643" value={flight} onChange={(e) => setFlight(e.currentTarget.value)} w={220} />
            <TextInput label="Violations folder (optional)" placeholder="newest violations*" value={vdir} onChange={(e) => setVdir(e.currentTarget.value)} w={240} />
            <Button onClick={() => imp.mutate()} loading={imp.isPending} disabled={!flight.trim()}>Import</Button>
          </Group>
          <Text size="xs" c="dimmed" mt={6}>
            Reads ml/data/results/recorded_flight_validation/&lt;flight&gt;/tracktrack_ours/violations*/ and uploads evidence clips.
          </Text>
        </Card>
      )}
      {sessions.isError && <Alert color="red">{(sessions.error as Error).message}</Alert>}
      <Card withBorder p={0}>
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr><Table.Th>Session</Table.Th><Table.Th>Source</Table.Th><Table.Th>Town</Table.Th><Table.Th>Started</Table.Th><Table.Th>Events</Table.Th><Table.Th>Profile</Table.Th><Table.Th /></Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {!sessions.data?.length && (
              <Table.Tr><Table.Td colSpan={7}><Text c="dimmed" p="md">{sessions.isLoading ? 'Loading…' : 'No sessions yet.'}</Text></Table.Td></Table.Tr>
            )}
            {sessions.data?.map((s) => (
              <Table.Tr key={s.session_id}>
                <Table.Td><Text fw={500} size="sm">{s.name || s.session_id}</Text></Table.Td>
                <Table.Td><Badge variant="light">{s.source}</Badge></Table.Td>
                <Table.Td>{s.town ?? '-'}</Table.Td>
                <Table.Td>{fmtDateTime(s.started_at)}</Table.Td>
                <Table.Td>{s.n_events}</Table.Td>
                <Table.Td>{s.profile ?? '-'}</Table.Td>
                <Table.Td>
                  <Group gap={4}>
                    <Button size="xs" variant="subtle" onClick={() => nav(`/live?session=${encodeURIComponent(s.session_id)}`)}>Replay</Button>
                    <Button size="xs" variant="subtle" onClick={() => nav(`/violations?session_id=${encodeURIComponent(s.session_id)}`)}>Events</Button>
                    <Button size="xs" variant="subtle" onClick={() => nav(`/twin?session=${encodeURIComponent(s.session_id)}`)}>3D twin</Button>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Card>
    </Stack>
  );
}
