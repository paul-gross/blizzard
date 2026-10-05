import type { ConfigChangeView } from 'fleet';
import { describe, expect, it } from 'vitest';

import {
  changeDetailEmptyText,
  changeEntries,
  changeEntryRows,
  changeEntryVm,
  entryByKey,
  entryTitle,
} from './changes.model';

function change(id: number, overrides: Partial<ConfigChangeView> = {}): ConfigChangeView {
  return {
    id,
    revision: 2,
    actor: 'pgross',
    door: 'cli',
    op: 'edit',
    record_kind: 'repository',
    record_key: 'winter',
    recorded_at: `2026-01-01T00:00:${String(id).padStart(2, '0')}Z`,
    diff: [{ field: 'base_branch', old: 'master', new: 'main' }],
    ...overrides,
  };
}

describe('changeEntries', () => {
  it('keeps every change its own entry while no apply_id is set, newest first', () => {
    const entries = changeEntries([change(9), change(8, { apply_id: null }), change(7)]);
    expect(entries.map((e) => e.key)).toEqual(['9', '8', '7']);
    expect(entries.every((e) => e.changes.length === 1)).toBe(true);
  });

  it('groups changes sharing an apply_id into one entry at its newest row', () => {
    const entries = changeEntries([
      change(9),
      change(8, { apply_id: 'a1', door: 'config apply' }),
      change(7, { apply_id: 'a1' }),
      change(6),
      change(5, { apply_id: 'a1' }),
      change(4, { apply_id: 'b2' }),
    ]);
    expect(entries.map((e) => e.key)).toEqual(['9', 'apply-a1', '6', 'apply-b2']);
    const apply = entries[1];
    expect(apply.changes.map((c) => c.id)).toEqual([8, 7, 5]);
    expect(apply.door).toBe('config apply');
    expect(apply.at).toBe('2026-01-01T00:00:08Z');
  });
});

describe('entryTitle', () => {
  it('names a single change by its record and op', () => {
    expect(entryTitle(changeEntries([change(1)])[0])).toBe('repository winter · edit');
  });

  it('counts an apply', () => {
    expect(entryTitle(changeEntries([change(2, { apply_id: 'a' }), change(1, { apply_id: 'a' })])[0])).toBe(
      'apply · 2 changes',
    );
    expect(entryTitle(changeEntries([change(1, { apply_id: 'a' })])[0])).toBe('apply · 1 change');
  });
});

describe('changeEntryRows', () => {
  it('maps each entry to a row with its actor and door', () => {
    expect(changeEntryRows(changeEntries([change(3, { record_kind: 'work_source', op: 'retire' })]))).toEqual([
      { key: '3', title: 'work source winter · retire', sub: ['pgross', 'via cli'], badges: [], revision: null, retired: false },
    ]);
  });
});

describe('entryByKey', () => {
  const entries = changeEntries([change(2), change(1)]);

  it('finds the entry a key names', () => {
    expect(entryByKey(entries, '1')?.changes[0].id).toBe(1);
  });

  it('answers null for no key or an unloaded one', () => {
    expect(entryByKey(entries, null)).toBeNull();
    expect(entryByKey(entries, '99')).toBeNull();
  });
});

describe('changeEntryVm', () => {
  it('answers null with no entry', () => {
    expect(changeEntryVm(null)).toBeNull();
  });

  it('carries each change with its route and diff', () => {
    const vm = changeEntryVm(changeEntries([change(5)])[0])!;
    expect(vm.rows).toEqual([
      {
        id: 5,
        title: 'repository winter · edit',
        route: ['/admin', 'repositories', 'winter'],
        diff: [{ field: 'base_branch', old: 'master', new: 'main' }],
        revisionStep: null,
      },
    ]);
  });

  it('renders a change with no field diff as its revision step', () => {
    const vm = changeEntryVm(
      changeEntries([change(5, { record_kind: 'secret', record_key: 'gh', op: 'replace', revision: 3, diff: [] })])[0],
    )!;
    expect(vm.rows[0].revisionStep).toBe('r2 → r3');
  });
});

describe('changeDetailEmptyText', () => {
  it('prompts with nothing selected, and explains an unloaded entry', () => {
    expect(changeDetailEmptyText(null)).toBe('Pick a change.');
    expect(changeDetailEmptyText('7')).toContain('older');
  });
});
