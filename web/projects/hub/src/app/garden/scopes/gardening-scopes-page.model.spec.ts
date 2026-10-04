import type { ScopeView } from 'fleet';
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

describe('scopeRows', () => {
  it('maps each scope, an unset retired flag reading false', () => {
    expect(scopeRows([BLIZZARD, DOCS])).toEqual([
      { slug: 'blizzard', description: 'The whole repo', retired: false },
      { slug: 'docs', description: 'Docs only', retired: true },
    ]);
  });
});
