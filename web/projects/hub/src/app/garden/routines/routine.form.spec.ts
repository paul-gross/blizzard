import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { EMPTY_ROUTINE_CREATE_DRAFT, type RoutineCreateDraft } from '../core/routine.domain';
import { routineCreateRules } from './routine.form';

function kinds(draft: Partial<RoutineCreateDraft>) {
  const f = form(signal<RoutineCreateDraft>({ ...EMPTY_ROUTINE_CREATE_DRAFT, ...draft }), routineCreateRules, {
    injector: TestBed.inject(Injector),
  });
  const of = (field: keyof RoutineCreateDraft) => f[field]().errors().map((e) => e.kind);
  return { slug: of('slug'), name: of('name'), graph: of('graph'), scope: of('scope'), urgency: of('urgency'), valid: f().valid() };
}

describe('the routine create rules', () => {
  it('require a slug, a graph, and a scope, and nothing else', () => {
    expect(kinds({})).toEqual({
      slug: ['required'],
      name: [],
      graph: ['required'],
      scope: ['required'],
      urgency: [],
      valid: false,
    });
    expect(kinds({ slug: 'nightly', graph: 'garden', scope: 'web' }).valid).toBe(true);
  });

  it('leave a malformed scope slug to the hub', () => {
    expect(kinds({ slug: 'nightly', graph: 'garden', scope: 'Bad Scope!' }).valid).toBe(true);
  });
});
