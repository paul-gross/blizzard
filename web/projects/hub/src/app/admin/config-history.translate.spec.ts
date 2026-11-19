import { describe, expect, it } from 'vitest';
import { configChangesPage, configChangeView } from 'fleet/testing';
import { toConfigChange, toConfigChangePage } from './config-history.translate';

describe('history projection', () => {
  it('retains revision, actor, and raw and labelled diff values', () => {
    const fields = { id: 5, revision: 3, actor: 'a', actor_name: null, apply_id: null, record_kind: 'project', record_key: 'proj_b', record_label: null, door: 'cli', op: 'edit', recorded_at: 'now',
      diff: [{ field: 'slug', old: 'before', new: 'after', old_label: null, new_label: 'After' }] };
    expect(toConfigChange(configChangeView(fields))).toEqual(fields);
  });
});

describe('change-log page projection', () => {
  it('carries the apply, record fields, cursor and total', () => {
    const change = configChangeView({ id: 7, apply_id: 'apl_1', record_kind: 'repository', record_key: 'winter', record_label: 'Winter' });
    expect(toConfigChangePage(configChangesPage({ changes: [change], next_before: 7, total: 12 }))).toEqual({
      changes: [toConfigChange(change)],
      next_before: 7,
      total: 12,
    });
    expect(toConfigChange(change)).toMatchObject({ apply_id: 'apl_1', record_kind: 'repository', record_key: 'winter', record_label: 'Winter' });
  });

  it('keeps a null cursor and an empty page', () => {
    expect(toConfigChangePage(configChangesPage())).toEqual({ changes: [], next_before: null, total: 0 });
  });
});
