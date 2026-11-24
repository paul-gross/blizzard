import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { EMPTY_RUN_DRAFT, type RunDraft } from '../core/run.domain';
import { runRules } from './run.form';

function kinds(draft: Partial<RunDraft>) {
  const f = form(
    signal<RunDraft>({ ...EMPTY_RUN_DRAFT, scope: 'web', ...draft }),
    runRules(() => new Set(['web', 'hub']), () => new Set(['web'])),
    { injector: TestBed.inject(Injector) },
  );
  const of = (field: keyof RunDraft) => f[field]().errors().map((e) => e.kind);
  return { scope: of('scope'), mode: of('mode'), valid: f().valid(), scopeRequired: f.scope().required(), modeRequired: f.mode().required() };
}

describe('the run rules', () => {
  it('require a scope, and one that is offered', () => {
    expect(kinds({ scope: '' })).toMatchObject({ scope: ['required'], scopeRequired: true, modeRequired: false });
    expect(kinds({ scope: 'gone' }).scope).toEqual(['scope']);
    expect(kinds({}).valid).toBe(true);
  });

  it('names a selected scope that is no longer offered', () => {
    const f = form(signal<RunDraft>({ ...EMPTY_RUN_DRAFT, scope: 'gone' }), runRules(() => new Set(['web']), () => new Set()), {
      injector: TestBed.inject(Injector),
    });
    expect(f.scope().errors()[0].message).toContain('no longer available');
  });

  it('block delta only for a scope without a baseline', () => {
    expect(kinds({ mode: 'delta', scope: 'web' }).valid).toBe(true);
    expect(kinds({ mode: 'delta', scope: 'hub' }).mode).toEqual(['mode']);
    expect(kinds({ mode: 'full', scope: 'hub' }).valid).toBe(true);
  });
});
