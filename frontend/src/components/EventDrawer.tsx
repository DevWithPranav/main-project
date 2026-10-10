// Event detail: evidence (clip, snapshot), values, tags, review history and confirm/dismiss.

import { Alert, Badge, Button, Code, Divider, Drawer, Group, Image, Stack, Table, Text, Textarea } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, mediaUrl } from '../api';
import { isAnomaly, type TrafficEvent } from '../api/types';
import { useAuth } from '../auth';
import { useConditions } from '../hooks';
import { conditionLabel, fmtDateTime, fmtSimTime, fmtValue, reviewState, STATUS_COLORS, statusLabel, TYPE_COLORS, typeLabel } from '../lib/format';
import { canReview } from '../lib/permissions';

export default function EventDrawer({ event, onClose }: { event: TrafficEvent | null; onClose: () => void }) {
  const { me } = useAuth();
  const { idx } = useConditions();
  const qc = useQueryClient();
  const [note, setNote] = useState('');
  const review = useMutation({
    mutationFn: (outcome: 'confirmed' | 'dismissed') => api.review(event!.event_id, outcome, note || undefined),
    onSuccess: (_, outcome) => {
      notifications.show({ color: outcome === 'confirmed' ? 'green' : 'gray', message: `Event ${outcome}` });
      setNote('');
      qc.invalidateQueries({ queryKey: ['events'] });
      qc.invalidateQueries({ queryKey: ['stats'] });
      onClose();
    },
    onError: (e: Error) => notifications.show({ color: 'red', title: 'Review failed', message: e.message }),
  });

  const e = event;
  const clip = mediaUrl(e?.evidence?.clip_url as string | undefined);
  const snap = mediaUrl(e?.evidence?.snapshot_url as string | undefined);
  const rows: [string, string][] = e
    ? isAnomaly(e)
      ? [['Time', fmtSimTime(e.t_s)], ['Frame', String(e.frame)], ['Severity', fmtValue(e.severity_score)], ['Area', e.area_sq_m ? `${e.area_sq_m.toFixed(2)} m²` : '-']]
      : [
          ['Condition', conditionLabel(e.condition, idx)],
          ['Vehicle', `${e.cls} #${e.track_ids.join(', #')}`],
          ['Started', fmtSimTime(e.start_s)],
          ['Flagged', fmtSimTime(e.flag_s)],
          ['Ended', e.end_s != null ? fmtSimTime(e.end_s) : 'ongoing / unknown'],
          ['Lane', e.lane_id ?? '-'],
          ...Object.entries(e.value ?? {}).map(([k, v]): [string, string] => [k.replace(/_/g, ' '), fmtValue(v)]),
        ]
    : [];

  return (
    <Drawer opened={!!e} onClose={onClose} position="right" size="lg" title={e ? typeLabel(e.type) : ''}>
      {e && (
        <Stack gap="sm">
          <Group gap="xs">
            <Badge color={TYPE_COLORS[e.type] ?? 'gray'}>{typeLabel(e.type)}</Badge>
            <Badge color={STATUS_COLORS[e.status] ?? 'gray'} variant="light">{statusLabel(e.status)}</Badge>
            <Badge variant="outline">conf {e.confidence.toFixed(2)}</Badge>
            {e.session_id && <Badge variant="default">{e.session_id}</Badge>}
          </Group>
          {clip ? (
            <video src={clip} controls autoPlay muted loop style={{ width: '100%', borderRadius: 8, background: '#000' }} />
          ) : snap ? (
            <Image src={snap} radius="md" />
          ) : (
            <Alert color="gray">No evidence clip for this event (render_violations.py --clips makes them).</Alert>
          )}
          <Table withRowBorders={false} verticalSpacing={4}>
            <Table.Tbody>
              {rows.map(([k, v]) => (
                <Table.Tr key={k}>
                  <Table.Td w={140}><Text size="sm" c="dimmed">{k}</Text></Table.Td>
                  <Table.Td><Text size="sm">{v}</Text></Table.Td>
                </Table.Tr>
              ))}
              <Table.Tr>
                <Table.Td><Text size="sm" c="dimmed">Position</Text></Table.Td>
                <Table.Td><Code>{e.x.toFixed(1)}, {e.y.toFixed(1)} m</Code></Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>
          {!!e.tags?.length && (
            <Group gap={4}>{e.tags.map((t) => <Badge key={t} size="xs" variant="dot">{t}</Badge>)}</Group>
          )}
          <Divider label="Review" />
          {e.review ? (
            <Alert color={e.review.outcome === 'confirmed' ? 'green' : 'gray'} title={`${e.review.outcome} by ${e.review.by}`}>
              {fmtDateTime(e.review.at)}{e.review.note ? ` · ${e.review.note}` : ''}
            </Alert>
          ) : (
            <Text size="sm" c="dimmed">Not reviewed yet.</Text>
          )}
          {canReview(me?.role, e) ? (
            <>
              <Textarea placeholder="Note (optional)" value={note} onChange={(ev) => setNote(ev.currentTarget.value)} autosize minRows={2} />
              <Group>
                <Button color="green" loading={review.isPending} onClick={() => review.mutate('confirmed')} disabled={reviewState(e) === 'confirmed'}>Confirm</Button>
                <Button color="gray" variant="light" loading={review.isPending} onClick={() => review.mutate('dismissed')} disabled={reviewState(e) === 'dismissed'}>Dismiss</Button>
              </Group>
            </>
          ) : (
            <Text size="xs" c="dimmed">Your role ({me?.role}) can view but not review this event.</Text>
          )}
        </Stack>
      )}
    </Drawer>
  );
}
