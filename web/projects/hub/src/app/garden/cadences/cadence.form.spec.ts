import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { type CadenceDraft, EMPTY_CADENCE_DRAFT } from '../core/cadence.domain';
import { cadenceCreateRules, cadenceEditRules } from './cadence.form';

function kinds(rules: typeof cadenceCreateRules, draft: Partial<CadenceDraft>) {
  const f = form(signal<CadenceDraft>({ ...EMPTY_CADENCE_DRAFT, ...draft }), rules, { injector: TestBed.inject(Injector) });
  const of = (field: keyof CadenceDraft) => f[field]().errors().map((e) => e.kind);
  return {
    root: f().errors().map((e) => e.kind),
    slug: of('slug'),
    cron: of('cron'),
    zone: of('zone'),
    spacing: of('spacing'),
    anchor: of('anchor'),
    valid: f().valid(),
  };
}

describe('the cadence rules', () => {
  it('require a slug on create only', () => {
    expect(kinds(cadenceCreateRules, { spacing: '1d' }).slug).toEqual(['required']);
    expect(kinds(cadenceCreateRules, { slug: 'evening', spacing: '1d' }).valid).toBe(true);
    expect(kinds(cadenceEditRules, { spacing: '1d' })).toMatchObject({ slug: [], valid: true });
  });

  it('pair a cron with a zone', () => {
    expect(kinds(cadenceEditRules, { cron: '0 20 * * *' }).zone).toEqual(['schedule']);
    expect(kinds(cadenceEditRules, { zone: 'UTC', spacing: '1d' }).zone).toEqual(['schedule']);
    expect(kinds(cadenceEditRules, { cron: '0 20 * * *', zone: 'UTC' }).valid).toBe(true);
  });

  it('read a cron of five fields only', () => {
    expect(kinds(cadenceEditRules, { cron: '0 20 * *', zone: 'UTC' }).cron).toEqual(['cron']);
  });

  it('read a spacing as a whole number above zero and m, h, or d', () => {
    for (const bad of ['0d', '10', '1.5h', '1w', '-1d']) expect(kinds(cadenceEditRules, { spacing: bad }).spacing).toEqual(['spacing']);
    for (const good of ['1m', '48h', '10d']) expect(kinds(cadenceEditRules, { spacing: good }).valid).toBe(true);
  });

  it('couple an anchor to a spacing', () => {
    expect(kinds(cadenceEditRules, { cron: '0 20 * * *', zone: 'UTC', anchor: 'finished' }).anchor).toEqual(['anchor']);
    expect(kinds(cadenceEditRules, { spacing: '1d', anchor: 'finished' }).valid).toBe(true);
  });

  it('refuse an empty cadence', () => {
    expect(kinds(cadenceCreateRules, { slug: 'evening' })).toMatchObject({ root: ['cadence'], valid: false });
    expect(kinds(cadenceEditRules, {}).root).toEqual(['cadence']);
  });
});
