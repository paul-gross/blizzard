import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { EMPTY_FORGE_DRAFT, type ForgeDraft } from './forge.domain';
import { forgeCreateRules, forgeEditRules } from './forge.form';

const FILLED: ForgeDraft = { slug: 'ghe', name: 'Enterprise', kind: 'github', apiBase: 'https://ghe.test', webBase: '' };

function judge(rules: typeof forgeCreateRules, draft: Partial<ForgeDraft>) {
  const f = form(signal<ForgeDraft>({ ...EMPTY_FORGE_DRAFT, ...draft }), rules, { injector: TestBed.inject(Injector) });
  const invalid = (field: keyof ForgeDraft) => f[field]().errors().length > 0;
  const marked = (field: keyof ForgeDraft) => f[field]().required();
  return { f, invalid, marked };
}

describe('the forge rules', () => {
  it('mark slug, kind, and API base required on create, and not name or web base', () => {
    const { marked } = judge(forgeCreateRules, {});
    expect(['slug', 'name', 'kind', 'apiBase', 'webBase'].map((k) => marked(k as keyof ForgeDraft))).toEqual([
      true, false, true, true, false,
    ]);
  });

  it('mark slug, name, and API base required on edit, and do not judge the kind', () => {
    const { marked, invalid } = judge(forgeEditRules, { ...FILLED, kind: '' });
    expect(['slug', 'name', 'kind', 'apiBase', 'webBase'].map((k) => marked(k as keyof ForgeDraft))).toEqual([
      true, true, false, true, false,
    ]);
    expect(invalid('kind')).toBe(false);
  });

  it('refuse a blank or whitespace required field and accept a filled draft', () => {
    expect(judge(forgeCreateRules, {}).f().valid()).toBe(false);
    expect(judge(forgeCreateRules, { ...FILLED, kind: '  ' }).invalid('kind')).toBe(true);
    expect(judge(forgeCreateRules, FILLED).f().valid()).toBe(true);
    expect(judge(forgeEditRules, { ...FILLED, name: '' }).invalid('name')).toBe(true);
    expect(judge(forgeEditRules, FILLED).f().valid()).toBe(true);
  });
});

