// Report history (PRD 14.1 GET /api/reports): every export made from the dashboard, re-downloadable.

import { Alert, Badge, Button, Modal, ScrollArea, Table, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api } from '../api';
import type { ReportRecord } from '../api/types';
import { fmtDateTime } from '../lib/format';

export function saveBlob(blob: Blob, filename: string) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

const size = (b: number) => (b < 1024 ? `${b} B` : b < 1 << 20 ? `${(b / 1024).toFixed(1)} kB` : `${(b / (1 << 20)).toFixed(1)} MB`);
const filterText = (f: Record<string, string>) =>
  Object.entries(f).map(([k, v]) => `${k}=${v}`).join(', ') || 'all events';

export default function ReportHistory({ opened, onClose }: { opened: boolean; onClose: () => void }) {
  const q = useQuery({ queryKey: ['reports'], queryFn: () => api.reports(100), enabled: opened, refetchOnMount: 'always' });
  const [busy, setBusy] = useState<string | null>(null);

  const download = async (r: ReportRecord) => {
    setBusy(r.id);
    try {
      const { blob, filename } = await api.reportFile(r);
      saveBlob(blob, filename);
    } catch (e) {
      notifications.show({ color: 'red', title: 'Download failed', message: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal opened={opened} onClose={onClose} title="Report history" size="xl">
      {q.error && <Alert color="red">{(q.error as Error).message}</Alert>}
      {q.data && !q.data.items.length && <Text c="dimmed">No reports yet: every export is kept here.</Text>}
      {!!q.data?.items.length && (
        <ScrollArea.Autosize mah={480}>
          <Table striped fz="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>When</Table.Th><Table.Th>By</Table.Th><Table.Th>Format</Table.Th><Table.Th>Filters</Table.Th>
                <Table.Th>Events</Table.Th><Table.Th>Size</Table.Th><Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {q.data.items.map((r) => (
                <Table.Tr key={r.id}>
                  <Table.Td>{fmtDateTime(r.at)}</Table.Td>
                  <Table.Td>{r.by}</Table.Td>
                  <Table.Td><Badge variant="light">{r.format.toUpperCase()}</Badge></Table.Td>
                  <Table.Td>{filterText(r.filters)}</Table.Td>
                  <Table.Td>{r.n_events < r.n_total ? `${r.n_events} of ${r.n_total}` : r.n_events}</Table.Td>
                  <Table.Td>{size(r.size_bytes)}</Table.Td>
                  <Table.Td>
                    <Button size="xs" variant="light" disabled={!r.stored} loading={busy === r.id} onClick={() => download(r)}>
                      Download
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea.Autosize>
      )}
    </Modal>
  );
}
