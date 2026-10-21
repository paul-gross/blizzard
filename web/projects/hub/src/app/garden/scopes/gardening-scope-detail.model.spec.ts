import type { RoutineView, ScopeView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { relatedRoutineRows, sameSlugProjects, scopeBySlug, scopeOverrideRetired, scopePanelVm } from './gardening-scope-detail.model';

const BLIZZARD: ScopeView = {
  slug: 'blizzard',
  description: 'The whole repo',
  created_at: '2026-01-01T00:00:00Z',
  retired: false,
  revision: null,
  project_id: null,
  project_name: null,
  project_slug: null,
};
const DOCS: ScopeView = {
  slug: 'docs',
  description: 'Docs only',
  created_at: '2026-01-01T00:00:00Z',
  retired: false,
  revision: null,
  project_id: null,
  project_name: null,
  project_slug: null,
};

const NIGHTLY: RoutineView = {
  routine_id: 'rtn_1',
  name: 'nightly',
  graph_name: 'garden-routine',
  default_scope_slug: 'blizzard',
  created_at: '2026-01-01T00:00:00Z',
  default_model: [],
  default_effort: null,
  default_harnesses: [],
  project_id: null,
  project_name: null,
  project_slug: null,
  retired: false,
  revision: null,
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
    expect(scopePanelVm(null, null, null, null)).toBeNull();
  });

  it('renders the real retired flag with nothing pending', () => {
    const related = [{ name: 'nightly', isDefault: true }];
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

  it('reads an unset retired flag as false', () => {
    expect(scopePanelVm(BLIZZARD, null, null, null)?.retired).toBe(false);
  });

  it('renders a pending override over the real retired flag', () => {
    const vm = scopePanelVm(BLIZZARD, true, null, null);
    expect(vm?.retired).toBe(false);
    expect(vm?.renderedRetired).toBe(true);
  });
});

describe('sameSlugProjects', () => {
  const scope = (slug: string, project_id: string | null, project_name: string | null) =>
    ({ slug, description: '', created_at: '2026-01-01T00:00:00Z', project_id, project_name }) as ScopeView;
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
