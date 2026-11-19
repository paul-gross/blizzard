import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import type { ScopeDescriptionDraft } from '../core/scope.domain';
import { SCOPE_DESCRIPTION_RULES } from './scope.form';

function description(value: string) {
  const f = form(signal<ScopeDescriptionDraft>({ description: value }), SCOPE_DESCRIPTION_RULES, { injector: TestBed.inject(Injector) });
  return { kinds: f.description().errors().map((e) => e.kind), required: f.description().required(), valid: f().valid() };
}

describe('the scope description rules', () => {
  it('require a description, and read blank text as none', () => {
    expect(description('')).toMatchObject({ kinds: ['required'], required: true });
    expect(description('   ').kinds).toEqual(['required']);
    expect(description('words').valid).toBe(true);
  });
});
