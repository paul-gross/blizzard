import type { RunRowView, ScopeView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { presentScopeSlug, scopeRows } from './gardening-scopes-page.model';

const BLIZZARD: ScopeView = { slug: 'blizzard', description: 'The whole repo', created_at: '2026-01-01T00:00:00Z' };
const DOCS: ScopeView = { slug: 'docs', description: 'Docs only', created_at: '2026-01-01T00:00:00Z', retired: true };

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
  };
}

describe('scopeRows', () => {
  it('maps each scope, an unset retired flag reading false and an unswept scope having no last sweep', () => {
    expect(scopeRows([BLIZZARD, DOCS], [])).toEqual([
      { slug: 'blizzard', description: 'The whole repo', retired: false, lastSwept: null },
      { slug: 'docs', description: 'Docs only', retired: true, lastSwept: null },
    ]);
  });

  it('dates each scope by the newest run that swept it', () => {
    const runs = [
      run('blizzard', '2026-03-01T00:00:00Z'),
      run('blizzard', '2026-03-05T00:00:00Z'),
      run('blizzard', '2026-03-03T00:00:00Z'),
      run('other', '2026-03-09T00:00:00Z'),
    ];
    expect(scopeRows([BLIZZARD, DOCS], runs).map((row) => row.lastSwept)).toEqual(['2026-03-05T00:00:00Z', null]);
  });
});
