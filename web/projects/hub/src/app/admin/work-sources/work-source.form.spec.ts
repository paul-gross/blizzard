import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { EMPTY_WORK_SOURCE_DRAFT, type WorkSourceDraft } from './work-source.domain';
import { workSourceCreateRules, workSourceEditRules } from './work-source.form';

function invalid(rules: typeof workSourceCreateRules, draft: Partial<WorkSourceDraft>) {
  const f = form(signal<WorkSourceDraft>({ ...EMPTY_WORK_SOURCE_DRAFT, ...draft }), rules, { injector: TestBed.inject(Injector) });
  return (Object.keys(EMPTY_WORK_SOURCE_DRAFT) as (keyof WorkSourceDraft)[]).filter((key) => f[key]().errors().length > 0);
}

const FILLED = { slug: 's', provider: 'github', locator: 'a/b', forge: 'frg_github' };

describe('the work-source rules', () => {
  for (const [name, rules] of [
    ['create', workSourceCreateRules],
    ['edit', workSourceEditRules],
  ] as const) {
    it(`require the slug, provider, locator, and forge on ${name}`, () => {
      expect(invalid(rules, {})).toEqual(['slug', 'provider', 'locator', 'forge']);
      expect(invalid(rules, { ...FILLED, forge: '' })).toEqual(['forge']);
      expect(invalid(rules, { ...FILLED, slug: '  ' })).toEqual(['slug']);
    });

    it(`leave the name, credential, and annotate optional on ${name}`, () => {
      expect(invalid(rules, FILLED)).toEqual([]);
    });
  }
});
