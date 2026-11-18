import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { EMPTY_URGENCY_DRAFT } from './urgency.domain';
import { urgencyCreateRules, urgencyEditRules } from './urgency.form';

describe('urgency form rules', () => {
  it('requires slug on create and name on both, but allows an empty color and mark', () => {
    const injector = TestBed.inject(Injector);
    const create = form(signal({ ...EMPTY_URGENCY_DRAFT }), urgencyCreateRules, { injector });
    expect(create.slug().errors().map((e) => e.kind)).toContain('required');
    expect(create.name().errors().map((e) => e.kind)).toContain('required');
    create.slug().value.set('  '); create.name().value.set('  ');
    expect(create().valid()).toBe(false);
    const edit = form(signal({ ...EMPTY_URGENCY_DRAFT, name: 'Soon' }), urgencyEditRules, { injector });
    expect(edit().valid()).toBe(true);
  });
  it('validates the create slug grammar and length, but not an immutable edit slug', () => {
    const injector = TestBed.inject(Injector);
    const create = form(signal({ ...EMPTY_URGENCY_DRAFT, name: 'Soon' }), urgencyCreateRules, { injector });
    for (const slug of ['Bad slug!', '-bad', 'bad-', 'bad--slug', 'a'.repeat(65)]) {
      create.slug().value.set(slug);
      expect(create.slug().errors().map((e) => e.kind)).toContain('slug');
    }
    for (const slug of ['a', 'a-1', 'a'.repeat(64), ' good-slug ']) {
      create.slug().value.set(slug);
      expect(create().valid()).toBe(true);
    }
    const edit = form(signal({ ...EMPTY_URGENCY_DRAFT, slug: 'Bad slug!', name: 'Soon' }), urgencyEditRules, { injector });
    expect(edit().valid()).toBe(true);
  });

  it('validates image bytes, encoded size, and emoji in both schemas', () => {
    const injector = TestBed.inject(Injector);
    const png = '\x89PNG\r\n\x1a\n';
    const url = (bytes: string) => `data:image/png;base64,${btoa(bytes)}`;
    for (const rules of [urgencyCreateRules, urgencyEditRules]) {
      const f = form(signal({ ...EMPTY_URGENCY_DRAFT, slug: 'soon', name: 'Soon' }), rules, { injector });
      for (const mark of [url(png + 'x'.repeat(32768 - png.length)), `data:image/webp;base64,${btoa('RIFFxxxxWEBP')}`, '🔥', '1️⃣', '']) {
        f.mark().value.set(mark);
        expect(f.mark().errors()).toEqual([]);
      }
      for (const mark of [url(png + 'x'.repeat(32769 - png.length)), url('not-png'), `data:image/webp;base64,${btoa(png)}`, 'data:image/png;base64,%%%','data:image/svg+xml;base64,PHN2Zz4=', 'hello', '12']) {
        f.mark().value.set(mark);
        expect(f.mark().errors().map((error) => error.kind)).toContain('mark');
      }
    }
  });

});
