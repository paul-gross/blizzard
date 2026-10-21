import type { RunRowView, ScopeView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { defaultRoutineWindow } from '../core/routine-window';
import { notSweptLabel, presentScopeSlug, scopeRows, scopeSwept } from './gardening-scopes-page.model';

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
  retired: true,
  revision: null,
  project_id: null,
  project_name: null,
  project_slug: null,
};

describe('presentScopeSlug', () => {
  it('answers null on the bare child route', () => {
    expect(presentScopeSlug(null, [BLIZZARD])).toBeNull();
  });

  it('answers the slug while it names a loaded scope', () => {
    expect(presentScopeSlug('docs', [BLIZZARD, DOCS])).toBe('docs');
  });

  it('answers null for a slug no loaded scope carries', () => {
    expect(presentScopeSlug('gone', [BLIZZARD])).toBeNull();
  });
});

function run(scope_slug: string, minted_at: string): RunRowView {
  return {
    chunk_id: `c-${scope_slug}-${minted_at}`,
    delivered: [],
    escalation: null,
    minted_at,
    mode: 'delta',
    outcome: 'done',
    routine_name: 'garden-weekly',
    scope_slug,
    project_id: null,
    project_name: null,
    project_slug: null,
  };
}

const WINDOW = defaultRoutineWindow(Date.parse('2026-03-10T00:00:00Z'));

describe('scopeRows', () => {
  it('maps each scope, an unset retired flag reading false', () => {
    expect(scopeRows([BLIZZARD, DOCS], [], WINDOW)).toEqual([
      { slug: 'blizzard', description: 'The whole repo', retired: false, swept: { at: null, notSwept: 'not in 28 days' } },
      { slug: 'docs', description: 'Docs only', retired: true, swept: { at: null, notSwept: 'not in 28 days' } },
    ]);
  });

  it('dates each scope by the newest run that swept it, and says the window holds none for the rest', () => {
    const runs = [
      run('blizzard', '2026-03-01T00:00:00Z'),
      run('blizzard', '2026-03-05T00:00:00Z'),
      run('blizzard', '2026-03-03T00:00:00Z'),
      run('other', '2026-03-09T00:00:00Z'),
    ];
    expect(scopeRows([BLIZZARD, DOCS], runs, WINDOW).map((row) => row.swept)).toEqual([
      { at: '2026-03-05T00:00:00Z', notSwept: 'not in 28 days' },
      { at: null, notSwept: 'not in 28 days' },
    ]);
  });

  it('says nothing of a sweep until the runs read answers', () => {
    expect(scopeRows([BLIZZARD], undefined, WINDOW).map((row) => row.swept)).toEqual([null]);
  });
});

describe('scopeSwept', () => {
  it('reads the newest run naming the scope, bounded by the window rather than ever saying never', () => {
    const runs = [run('blizzard', '2026-03-01T00:00:00Z'), run('blizzard', '2026-03-04T00:00:00Z')];
    expect(scopeSwept('blizzard', runs, WINDOW)).toEqual({ at: '2026-03-04T00:00:00Z', notSwept: 'not in 28 days' });
    expect(scopeSwept('docs', runs, WINDOW)).toEqual({ at: null, notSwept: 'not in 28 days' });
    expect(scopeSwept('blizzard', undefined, WINDOW)).toBeNull();
  });

  it('names the window\'s own reach', () => {
    expect(notSweptLabel(WINDOW)).toBe('not in 28 days');
  });
});
