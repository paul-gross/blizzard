import type { Scope, ScopeRoutine } from '../core/scope.domain';
import { describe, expect, it } from 'vitest';

import { relatedRoutineRows, sameSlugProjects, scopeBySlug, scopeOverrideRetired, scopePanelVm } from './gardening-scope-detail.model';

const BLIZZARD: Scope = { slug: 'blizzard', description: 'The whole repo', retired: false, projectId: null, projectName: null };
const DOCS: Scope = { slug: 'docs', description: 'Docs only', retired: false, projectId: null, projectName: null };

const NIGHTLY: ScopeRoutine = { routineId: 'rtn_1', slug: 'nightly', name: 'Nightly sweep', defaultScopeSlug: 'blizzard' };
const WEEKLY: ScopeRoutine = { routineId: 'rtn_2', slug: 'weekly', name: 'Weekly sweep', defaultScopeSlug: 'docs' };

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

  it("resolves each id to its routine's slug and display name and marks this scope's own defaults", () => {
    expect(relatedRoutineRows(['rtn_1', 'rtn_2'], BLIZZARD, [NIGHTLY, WEEKLY])).toEqual([
      { slug: 'nightly', name: 'Nightly sweep', isDefault: true },
      { slug: 'weekly', name: 'Weekly sweep', isDefault: false },
    ]);
  });

  it('falls back to the raw id for a routine the routine read does not carry', () => {
    expect(relatedRoutineRows(['rtn_9'], BLIZZARD, [NIGHTLY])).toEqual([{ slug: 'rtn_9', name: 'rtn_9', isDefault: false }]);
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
    expect(scopePanelVm(null, null, null, null)).toBeNull();
  });

  it('renders the real retired flag with nothing pending', () => {
    const related = [{ slug: 'nightly', name: 'Nightly sweep', isDefault: true }];
    const swept = { at: null, notSwept: 'not in 28 days' };
    expect(scopePanelVm({ ...BLIZZARD, retired: true }, null, related, swept)).toEqual({
      slug: 'blizzard',
      description: 'The whole repo',
      retired: true,
      renderedRetired: true,
      relatedRoutines: related,
      swept,
    });
  });

  it('renders a pending override over the real retired flag', () => {
    const vm = scopePanelVm(BLIZZARD, true, null, null);
    expect(vm?.retired).toBe(false);
    expect(vm?.renderedRetired).toBe(true);
  });
});

describe('sameSlugProjects', () => {
  const scope = (slug: string, projectId: string | null, projectName: string | null): Scope => ({
    slug,
    description: '',
    retired: false,
    projectId,
    projectName,
  });
  const HUB_API = scope('hub-api', 'p_b', 'blizzard');

  it('names every other project holding a scope of the same slug, by name', () => {
    const tenant = [HUB_API, scope('hub-api', 'p_w', 'winter'), scope('cli', 'p_w', 'winter'), scope('hub-api', 'p_a', 'alpha')];
    expect(sameSlugProjects(HUB_API, 'p_b', tenant)).toEqual(['alpha', 'winter']);
  });

  it('names none when only this project holds the slug, or before the scope and project are known', () => {
    expect(sameSlugProjects(HUB_API, 'p_b', [HUB_API, scope('cli', 'p_w', 'winter')])).toEqual([]);
    expect(sameSlugProjects(null, 'p_b', [HUB_API])).toEqual([]);
    expect(sameSlugProjects(HUB_API, null, [HUB_API, scope('hub-api', 'p_w', 'winter')])).toEqual([]);
  });
});
