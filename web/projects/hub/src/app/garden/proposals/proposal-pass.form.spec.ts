import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { emptyPassDraft } from './proposal-pass.domain';
import { proposalPassRules } from './proposal-pass.form';

describe('proposalPassRules', () => {
  it('requires a reason, counts whitespace as blank, and marks the field required', () => {
    const f = form(signal(emptyPassDraft()), proposalPassRules, {
      injector: TestBed.inject(Injector),
    });
    expect(f.reason().required()).toBe(true);
    expect(
      f
        .reason()
        .errors()
        .map((e) => e.message),
    ).toEqual(['Required']);
    f.reason().value.set('   ');
    expect(
      f
        .reason()
        .errors()
        .map((e) => e.message),
    ).toEqual(['Required']);
    f.reason().value.set('why');
    expect(f.reason().errors()).toEqual([]);
    expect(f().valid()).toBe(true);
  });
});
