import type { ConfigChangeView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { changesOfPages, historyRecordKey, lastChange, revisionRows } from './config-history.model';

function change(id: number, revision: number, overrides: Partial<ConfigChangeView> = {}): ConfigChangeView {
  return {
    id,
    revision,
    actor: 'pgross',
    door: 'cli',
    op: 'edit',
    record_kind: 'work_source',
    record_key: 'winter',
    recorded_at: `2026-01-0${revision}T00:00:00Z`,
    diff: [],
    ...overrides,
  };
}

describe('lastChange', () => {
  it('reads the newest row', () => {
    const history = [change(9, 2, { door: 'board', actor: 'ana' }), change(3, 1)];
    expect(lastChange(history)).toEqual({ revision: 2, at: '2026-01-02T00:00:00Z', actor: 'ana', door: 'board' });
  });

  it('answers null before the history resolves or when it is empty', () => {
    expect(lastChange(undefined)).toBeNull();
    expect(lastChange([])).toBeNull();
  });
});

describe('revisionRows', () => {
  it('maps each change, joining the fields it changed', () => {
    const rows = revisionRows([
      change(9, 2, { diff: [{ field: 'annotate', old: false, new: true }, { field: 'secret', old: 'a', new: 'b' }] }),
      change(3, 1, { op: 'create' }),
    ]);
    expect(rows.map((r) => [r.revision, r.op, r.fields])).toEqual([
      [2, 'edit', 'annotate, secret'],
      [1, 'create', ''],
    ]);
  });

  it('answers no rows before the history resolves', () => {
    expect(revisionRows(undefined)).toEqual([]);
  });
});

describe('changesOfPages', () => {
  it('flattens pages in order', () => {
    expect(changesOfPages([{ changes: [change(5, 2)] }, { changes: [change(4, 1)] }]).map((c) => c.id)).toEqual([5, 4]);
    expect(changesOfPages(undefined)).toEqual([]);
  });
});

describe('historyRecordKey', () => {
  it('keys a configured record by name', () => {
    expect(historyRecordKey({ name: 'winter' })).toBe('winter');
  });

  it('keeps the read at rest for no record or a built-in one', () => {
    expect(historyRecordKey(null)).toBeNull();
    expect(historyRecordKey({ name: 'hub', built_in: true })).toBeNull();
  });
});
