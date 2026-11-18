import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { EMPTY_SECRET_CREATE_DRAFT, type SecretCreateDraft } from './secret.domain';
import { secretCreateRules, secretReplaceRules } from './secret.form';

function invalidCreate(draft: Partial<SecretCreateDraft>) {
  const f = form(signal<SecretCreateDraft>({ ...EMPTY_SECRET_CREATE_DRAFT, ...draft }), secretCreateRules, {
    injector: TestBed.inject(Injector),
  });
  return (['name', 'value'] as const).filter((key) => f[key]().errors().length > 0);
}

describe('the secret rules', () => {
  it('require the name and the value on create', () => {
    expect(invalidCreate({})).toEqual(['name', 'value']);
    expect(invalidCreate({ name: 'tok' })).toEqual(['value']);
    expect(invalidCreate({ name: '  ', value: 'x' })).toEqual(['name']);
    expect(invalidCreate({ name: 'tok', value: 'x' })).toEqual([]);
  });

  it('require the value on a replacement', () => {
    const f = form(signal({ value: '' }), secretReplaceRules, { injector: TestBed.inject(Injector) });
    expect(f.value().errors().length).toBeGreaterThan(0);
    f.value().value.set('x');
    expect(f.value().errors()).toEqual([]);
  });
});
