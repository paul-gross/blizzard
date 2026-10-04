import type { RoutineBaselineView, ScopeView } from 'fleet';
import { describe, expect, it } from 'vitest';

import { liveRelatedScopes, orderScopesBySwept, sweptScopeSlugs } from './gardening-run-dialog.model';

function scope(slug: string, retired?: boolean): ScopeView {
  return { slug, description: `${slug} scope`, created_at: '2026-01-01T00:00:00Z', retired };
}

function baseline(scopeSlug: string): RoutineBaselineView {
  return { scope_slug: scopeSlug, finding_set_id: `fins_${scopeSlug}`, recorded_at: '2026-01-10T00:00:00Z', repos: [] };
}

const ALPHA = scope('alpha');
const BETA = scope('beta');
const GAMMA = scope('gamma');

describe('liveRelatedScopes', () => {
  it('keeps related, non-retired scopes in their own order', () => {
    expect(liveRelatedScopes([ALPHA, BETA, GAMMA], new Set(['gamma', 'alpha']))).toEqual([ALPHA, GAMMA]);
  });

  it('drops a retired scope even while it is related', () => {
    expect(liveRelatedScopes([ALPHA, scope('beta', true)], new Set(['alpha', 'beta']))).toEqual([ALPHA]);
  });

  it('answers none with nothing related', () => {
    expect(liveRelatedScopes([ALPHA, BETA], new Set())).toEqual([]);
  });
});

describe('sweptScopeSlugs', () => {
  it('collects every baseline scope slug', () => {
    expect([...sweptScopeSlugs([baseline('beta'), baseline('alpha')])]).toEqual(['beta', 'alpha']);
  });

  it('answers an empty set with no baselines', () => {
    expect(sweptScopeSlugs([]).size).toBe(0);
  });
});

describe('orderScopesBySwept', () => {
  it('puts swept scopes first in baseline order, then the rest in their own order', () => {
    const baselines = [baseline('gamma'), baseline('alpha')];
    expect(orderScopesBySwept([ALPHA, BETA, GAMMA], baselines, sweptScopeSlugs(baselines))).toEqual([
      GAMMA,
      ALPHA,
      BETA,
    ]);
  });

  it('drops a baseline whose scope is no longer live', () => {
    const baselines = [baseline('retired-one'), baseline('beta')];
    expect(orderScopesBySwept([ALPHA, BETA], baselines, sweptScopeSlugs(baselines))).toEqual([BETA, ALPHA]);
  });

  it('keeps the live order with no baselines', () => {
    expect(orderScopesBySwept([BETA, ALPHA], [], new Set())).toEqual([BETA, ALPHA]);
  });
});
