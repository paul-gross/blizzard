import { changeEntriesOf, changesOf, HUB_SETTING_FIELD_VIEWS, recordOf, revisionsOf } from './hub-settings.model';
import type { HubSettingsChange, HubSettingRow } from 'fleet';

it('derives records, revisions and field values', () => {
  const row: HubSettingRow = { key: 'sweep', value: 60, lifetime: 'pass', source: 'default', revision: null, written_at: null, written_by: null };
  const change: HubSettingsChange = { id: 1, revision: 2, op: 'edit', door: 'api', actor: 'a', recorded_at: '2026-01-01', apply_id: null,
    diff: [{ field: 'sweep', old: 60, new: 75, old_label: null, new_label: null }] };
  expect(recordOf(row).facts).toEqual([{ label: 'Value', value: '60' }, { label: 'Source', value: 'default' }, { label: 'Lifetime', value: 'pass' }]);
  expect(changesOf(undefined)).toEqual([]);
  expect(revisionsOf(changesOf([{ changes: [change] }]))[0].revision).toBe(2);
});


it('maps each change to a change entry with its one row, and offers the default choice on the value', () => {
  const diff = [{ field: 'sweep', old: 60, new: 75, old_label: null, new_label: null }];
  const change: HubSettingsChange = { id: 4, revision: 2, op: 'edit', door: 'api', actor: 'a', recorded_at: '2026-01-01', apply_id: null, diff };
  expect(changeEntriesOf([change])).toEqual([
    { title: 'Revision 2 · edit', actor: 'a', door: 'api', at: '2026-01-01',
      rows: [{ id: 4, title: 'hub settings', route: null, diff, revisionStep: null }] },
  ]);
  expect(HUB_SETTING_FIELD_VIEWS).toEqual([{ key: 'value', kind: 'text', label: 'Value', orDefault: { key: 'useDefault', label: 'Default' } }]);
});
