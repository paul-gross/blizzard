import { changesOf, recordOf, revisionsOf } from './hub-settings.model';
import type { HubSettingsChange, HubSettingRow } from 'fleet';

it('derives records, revisions and field values', () => {
  const row: HubSettingRow = { key: 'sweep', value: 60, lifetime: 'pass', source: 'default', revision: null, written_at: null, written_by: null };
  const change: HubSettingsChange = { id: 1, revision: 2, op: 'edit', door: 'api', actor: 'a', recorded_at: '2026-01-01', apply_id: null,
    diff: [{ field: 'sweep', old: 60, new: 75, old_label: null, new_label: null }] };
  expect(recordOf(row).facts).toEqual([{ label: 'Value', value: '60' }, { label: 'Source', value: 'default' }, { label: 'Lifetime', value: 'pass' }]);
  expect(changesOf(undefined)).toEqual([]);
  expect(revisionsOf(changesOf([{ changes: [change] }]))[0].revision).toBe(2);
});

