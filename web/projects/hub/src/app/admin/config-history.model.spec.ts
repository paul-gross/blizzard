import type { ConfigChangeView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { changeActor, changesOfPages, historyRecordKey, lastChange, revisionRows } from './config-history.model';

function change(id: number, revision: number, overrides: Partial<ConfigChangeView> = {}): ConfigChangeView {
  return {
    id,
    revision,
    actor: 'pgross',
    door: 'cli',
    actor_name: null,
    apply_id: null,
    project_id: null,
    record_label: null,
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

  it("names its author by the hub's name for them", () => {
    expect(lastChange([change(9, 2, { actor: 'usr_1', actor_name: 'Ana' })])?.actor).toBe('Ana');
  });

  it('answers null before the history resolves or when it is empty', () => {
    expect(lastChange(undefined)).toBeNull();
    expect(lastChange([])).toBeNull();
  });
});

describe('revisionRows', () => {
  it('names the forge by its labels instead of stored ids in a revision', () => {
    const rows = revisionRows([change(10, 3, { diff: [{
      field: 'forge', old: 'frg_1', new: 'frg_2', old_label: 'github · GitHub', new_label: 'enterprise · Enterprise',
    }] })]);
    expect(rows[0].fields).toBe('forge: github · GitHub → enterprise · Enterprise');
  });
  it('maps each change, joining the fields it changed', () => {
    const rows = revisionRows([
      change(9, 2, { diff: [{ field: 'annotate', old: false, new: true, old_label: null, new_label: null }, { field: 'secret', old: 'a', new: 'b', old_label: null, new_label: null }] }),
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

  it("names each author by the hub's name for them", () => {
    const rows = revisionRows([change(9, 2, { actor: 'usr_1', actor_name: 'Ada Lovelace' }), change(3, 1)]);
    expect(rows.map((r) => r.actor)).toEqual(['Ada Lovelace', 'pgross']);
  });
});

describe('changeActor', () => {
  it("reads the hub's name for the actor", () => {
    expect(changeActor(change(1, 1, { actor: 'usr_1', actor_name: 'Ada Lovelace' }))).toBe('Ada Lovelace');
  });

  it('falls back to the actor as recorded when the hub names no member', () => {
    expect(changeActor(change(1, 1, { actor: 'migration', actor_name: null }))).toBe('migration');
    expect(changeActor(change(1, 1, { actor: 'migration' }))).toBe('migration');
  });
});

describe('changesOfPages', () => {
  it('flattens pages in order', () => {
    expect(changesOfPages([{ changes: [change(5, 2)], total: 2, next_before: 4 }, { changes: [change(4, 1)], total: 2, next_before: null }]).map((c) => c.id)).toEqual([5, 4]);
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
