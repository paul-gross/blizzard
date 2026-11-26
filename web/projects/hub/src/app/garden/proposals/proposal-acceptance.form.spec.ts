import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { acceptanceDraftFor } from './proposal-acceptance.domain';
import { proposalAcceptanceRules } from './proposal-acceptance.form';

function acceptanceForm() {
  return form(signal(acceptanceDraftFor('the body')), proposalAcceptanceRules, {
    injector: TestBed.inject(Injector),
  });
}

const messages = (errors: readonly { message?: string }[]) => errors.map((e) => e.message);

describe('proposalAcceptanceRules', () => {
  it('leaves a mint valid with every optional field empty, and its body and urgency enabled and shown', () => {
    const f = acceptanceForm();
    f.body().value.set('');
    expect(f.reason().required()).toBe(false);
    expect(f().valid()).toBe(true);
    expect(f.body().hidden()).toBe(false);
    expect(f.body().disabled()).toBe(false);
    expect(f.urgency().hidden()).toBe(false);
    expect(f.urgency().disabled()).toBe(false);
  });

  it('does not judge a whitespace reason under a mint', () => {
    const f = acceptanceForm();
    f.reason().value.set('   ');
    expect(f().valid()).toBe(true);
  });

  it('requires a nonblank reason under a decline, counting whitespace as blank', () => {
    const f = acceptanceForm();
    f.mode().value.set('decline');
    expect(f.reason().required()).toBe(true);
    expect(messages(f.reason().errors())).toEqual(['Required']);
    f.reason().value.set('   ');
    expect(messages(f.reason().errors())).toEqual(['Required']);
    f.reason().value.set('already tracked');
    expect(f.reason().errors()).toEqual([]);
    expect(f().valid()).toBe(true);
  });

  it('disables and hides the body and urgency under a decline, and restores them on switching back', () => {
    const f = acceptanceForm();
    f.mode().value.set('decline');
    expect(f.body().hidden()).toBe(true);
    expect(f.body().disabled()).toBe(true);
    expect(f.urgency().hidden()).toBe(true);
    expect(f.urgency().disabled()).toBe(true);
    f.mode().value.set('mint');
    expect(f.body().hidden()).toBe(false);
    expect(f.urgency().disabled()).toBe(false);
    expect(f.reason().required()).toBe(false);
  });
});
