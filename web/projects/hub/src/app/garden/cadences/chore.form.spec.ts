import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { type ChoreDraft, EMPTY_CHORE_DRAFT } from '../core/cadence.domain';
import { choreAddRules, choreEditRules } from './chore.form';

function kinds(rules: typeof choreAddRules, draft: Partial<ChoreDraft>) {
  const f = form(signal<ChoreDraft>({ ...EMPTY_CHORE_DRAFT, key: 'nightly', routine: 'nightly', ...draft }), rules, {
    injector: TestBed.inject(Injector),
  });
  const of = (field: keyof ChoreDraft) => f[field]().errors().map((e) => e.kind);
  return { key: of('key'), routine: of('routine'), scopes: of('scopes'), valid: f().valid() };
}

describe('the chore rules', () => {
  it('require a routine', () => {
    expect(kinds(choreEditRules, { routine: '' }).routine).toEqual(['required']);
    expect(kinds(choreEditRules, {}).valid).toBe(true);
  });

  it('require a slug key on add only', () => {
    expect(kinds(choreAddRules, { key: '' }).key).toEqual(['required']);
    expect(kinds(choreAddRules, { key: 'Not A Slug' }).key).toEqual(['key']);
    expect(kinds(choreAddRules, { key: 'weekly-2' }).valid).toBe(true);
    expect(kinds(choreEditRules, { key: '' })).toMatchObject({ key: [], valid: true });
  });

  it('read scopes as blank, all, or slugs', () => {
    for (const good of ['', 'all', 'web', 'web, hub', 'web hub-2']) expect(kinds(choreEditRules, { scopes: good }).valid).toBe(true);
    for (const bad of ['web, Hub', 'a/b', 'all, web!']) expect(kinds(choreEditRules, { scopes: bad }).scopes).toEqual(['scopes']);
  });
});
