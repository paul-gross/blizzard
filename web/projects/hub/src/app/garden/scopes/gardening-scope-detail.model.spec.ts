import type { RoutineView, ScopeView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { relatedRoutineRows, scopeBySlug, scopeOverrideRetired, scopePanelVm } from './gardening-scope-detail.model';

const BLIZZARD: ScopeView = { slug: 'blizzard', description: 'The whole repo', created_at: '2026-01-01T00:00:00Z' };
const DOCS: ScopeView = { slug: 'docs', description: 'Docs only', created_at: '2026-01-01T00:00:00Z' };

const NIGHTLY: RoutineView = {
  routine_id: 'rtn_1',
  name: 'nightly',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  created_at: '2026-01-01T00:00:00Z',
};
const WEEKLY: RoutineView = { ...NIGHTLY, routine_id: 'rtn_2', name: 'weekly', default_scope_slug: 'docs' };

describe('scopeBySlug', () => {
  it('answers null on the bare child route', () => {
    expect(scopeBySlug(null, [BLIZZARD])).toBeNull();
  });

  it('finds the scope the slug names', () => {
    expect(scopeBySlug('docs', [BLIZZARD, DOCS])).toBe(DOCS);
  });

  it('answers null for a slug no loaded scope carries', () => {
    expect(scopeBySlug('gone', [BLIZZARD])).toBeNull();
  });
});

describe('relatedRoutineRows', () => {
  it('answers null until the related-routines read resolves', () => {
    expect(relatedRoutineRows(undefined, BLIZZARD, [NIGHTLY])).toBeNull();
  });

  it('answers null with no scope', () => {
    expect(relatedRoutineRows(['rtn_1'], null, [NIGHTLY])).toBeNull();
  });

  it('answers null until the routine read resolves', () => {
    expect(relatedRoutineRows(['rtn_1'], BLIZZARD, undefined)).toBeNull();
  });

  it("resolves each id to its routine's name and marks this scope's own defaults", () => {
    expect(relatedRoutineRows(['rtn_1', 'rtn_2'], BLIZZARD, [NIGHTLY, WEEKLY])).toEqual([
      { name: 'nightly', isDefault: true },
      { name: 'weekly', isDefault: false },
    ]);
  });

  it('falls back to the raw id for a routine the routine read does not carry', () => {
    expect(relatedRoutineRows(['rtn_9'], BLIZZARD, [NIGHTLY])).toEqual([{ name: 'rtn_9', isDefault: false }]);
  });
});

describe('scopeOverrideRetired', () => {
  it('answers null with no scope', () => {
    expect(scopeOverrideRetired(null, [{ slug: 'blizzard', retired: true }])).toBeNull();
  });

  it('answers null when nothing is pending for the scope', () => {
    expect(scopeOverrideRetired(BLIZZARD, [{ slug: 'docs', retired: true }])).toBeNull();
  });

  it("answers the pending mutation's retired flag", () => {
    expect(scopeOverrideRetired(BLIZZARD, [{ slug: 'blizzard', retired: true }])).toBe(true);
    expect(scopeOverrideRetired(BLIZZARD, [{ slug: 'blizzard', retired: false }])).toBe(false);
  });
});

describe('scopePanelVm', () => {
  it('answers null with no scope selected', () => {
    expect(scopePanelVm(null, null, null)).toBeNull();
  });

  it('renders the real retired flag with nothing pending', () => {
    const related = [{ name: 'nightly', isDefault: true }];
    expect(scopePanelVm({ ...BLIZZARD, retired: true }, null, related)).toEqual({
      slug: 'blizzard',
      description: 'The whole repo',
      retired: true,
      renderedRetired: true,
      relatedRoutines: related,
    });
  });

  it('reads an unset retired flag as false', () => {
    expect(scopePanelVm(BLIZZARD, null, null)?.retired).toBe(false);
  });

  it('renders a pending override over the real retired flag', () => {
    const vm = scopePanelVm(BLIZZARD, true, null);
    expect(vm?.retired).toBe(false);
    expect(vm?.renderedRetired).toBe(true);
  });
});
