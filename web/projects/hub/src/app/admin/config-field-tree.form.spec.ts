import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form, required, schema } from '@angular/forms/signals';

import { applyShownWhen } from './config-field-tree.form';
import type { ConfigFieldView } from './config-field-tree.model';

interface Draft {
  kind: string;
  detail: string;
}

const FIELDS: readonly ConfigFieldView<Draft>[] = [
  { key: 'kind', label: 'Kind', kind: 'text' },
  { key: 'detail', label: 'Detail', kind: 'text', shownWhen: { key: 'kind', value: 'b' } },
];

const rules = schema<Draft>((p) => {
  applyShownWhen(p, FIELDS);
  required(p.detail);
});

describe('applyShownWhen', () => {
  it('hides a field while its condition fails, keeping it out of validity, and shows it when it holds', () => {
    const f = TestBed.runInInjectionContext(() => form(signal<Draft>({ kind: 'a', detail: '' }), rules));
    expect(f.detail().hidden()).toBe(true);
    expect(f().valid()).toBe(true);
    f.kind().value.set('b');
    expect(f.detail().hidden()).toBe(false);
    expect(f().valid()).toBe(false);
    expect(f.kind().hidden()).toBe(false);
  });
});
