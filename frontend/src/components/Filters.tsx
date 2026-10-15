// Filter bar shared by Violations and Statistics; state lives in the URL so views can be shared.

import { Button, Group, Menu, Select, TextInput } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconDownload, IconHistory } from '@tabler/icons-react';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api';
import type { ExportFormat } from '../api/client';
import { VIOLATION_STATUSES, VIOLATION_TYPES } from '../api/types';
import { useConditions, useSessions } from '../hooks';
import { activeFilterCount, filtersToParams, paramsToFilters, type EventFilters } from '../lib/filters';
import { statusLabel, typeLabel } from '../lib/format';
import ReportHistory, { saveBlob } from './ReportHistory';

/** ISO instant -> the browser's local 'YYYY-MM-DDTHH:mm' for a datetime-local input, and back. */
function isoToLocal(iso: string | undefined): string {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return '';
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

/** endOfMinute: 'To 14:30' keeps events up to 14:30:59.999, not only those before 14:30:00. */
function localToIso(local: string, endOfMinute = false): string | undefined {
  const d = new Date(local);
  return local && !Number.isNaN(d.getTime()) ? new Date(d.getTime() + (endOfMinute ? 59999 : 0)).toISOString() : undefined;
}

export function useFilters(): [EventFilters, (f: EventFilters) => void] {
  const [sp, setSp] = useSearchParams();
  return [paramsToFilters(sp), (f) => setSp(filtersToParams(f), { replace: true })];
}

export default function FilterBar({ value, onChange }: { value: EventFilters; onChange: (f: EventFilters) => void }) {
  const sessions = useSessions();
  const { data: conds } = useConditions();
  const [busy, setBusy] = useState<ExportFormat | null>(null);
  const [history, setHistory] = useState(false);
  const set = (k: keyof EventFilters) => (v: string | null) => onChange({ ...value, [k]: v || undefined });

  const download = async (fmt: ExportFormat) => {
    setBusy(fmt);
    const t0 = performance.now();
    try {
      const { blob, filename } = await api.exportFile(fmt, value);
      saveBlob(blob, filename);
      notifications.show({ color: 'green', message: `${filename} ready in ${((performance.now() - t0) / 1000).toFixed(1)} s` });
    } catch (e) {
      notifications.show({ color: 'red', title: 'Export failed', message: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  // road-surface anomalies (M9) were dropped from the project (2026-10-10): violations only
  const types = VIOLATION_TYPES;
  const statuses = VIOLATION_STATUSES;

  return (
    <Group gap="xs" align="end">
      <Select label="Session" w={200} clearable searchable value={value.session_id ?? null} onChange={set('session_id')}
        data={(sessions.data ?? []).map((s) => ({ value: s.session_id, label: s.name || s.session_id }))} />
      <Select label="Type" w={170} clearable value={value.type ?? null} onChange={set('type')} data={types.map((t) => ({ value: t, label: typeLabel(t) }))} />
      <Select label="Condition" w={230} clearable searchable value={value.condition ?? null} onChange={set('condition')}
        data={(conds?.conditions ?? []).filter((c) => !c.alias_of).map((c) => ({ value: c.id, label: `${c.id} ${c.name}` }))} />
      <Select label="Status" w={150} clearable value={value.status ?? null} onChange={set('status')} data={statuses.map((s) => ({ value: s, label: statusLabel(s) }))} />
      <Select label="Review" w={130} clearable value={value.review ?? null} onChange={set('review')}
        data={[{ value: 'none', label: 'not reviewed' }, { value: 'confirmed', label: 'confirmed' }, { value: 'dismissed', label: 'dismissed' }]} />
      <TextInput label="From" type="datetime-local" w={200} value={isoToLocal(value.since)}
        max={isoToLocal(value.until) || undefined} onChange={(ev) => set('since')(localToIso(ev.currentTarget.value) ?? null)} />
      <TextInput label="To" type="datetime-local" w={200} value={isoToLocal(value.until)}
        min={isoToLocal(value.since) || undefined} onChange={(ev) => set('until')(localToIso(ev.currentTarget.value, true) ?? null)} />
      {activeFilterCount(value) > 0 && <Button variant="subtle" onClick={() => onChange({})}>Clear</Button>}
      <Menu>
        <Menu.Target>
          <Button leftSection={<IconDownload size={16} />} variant="light" loading={!!busy}>Export</Button>
        </Menu.Target>
        <Menu.Dropdown>
          {(['pdf', 'xlsx', 'csv', 'geojson'] as ExportFormat[]).map((f) => (
            <Menu.Item key={f} onClick={() => download(f)}>{f.toUpperCase()}</Menu.Item>
          ))}
          <Menu.Divider />
          <Menu.Item leftSection={<IconHistory size={14} />} onClick={() => setHistory(true)}>Report history</Menu.Item>
        </Menu.Dropdown>
      </Menu>
      <ReportHistory opened={history} onClose={() => setHistory(false)} />
    </Group>
  );
}
