import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { EMPTY_ANSWER_DRAFT } from './awaiting-human.domain';
import { answerRules } from './awaiting-human.form';

describe('answerRules', () => {
  it('requires an answer, counts whitespace as blank, and marks the field required', () => {
    const f = form(signal(EMPTY_ANSWER_DRAFT), answerRules, { injector: TestBed.inject(Injector) });
    const messages = () =>
      f
        .answer()
        .errors()
        .map((e) => e.message);

    expect(f.answer().required()).toBe(true);
    expect(messages()).toEqual(['Required']);
    f.answer().value.set('   ');
    expect(messages()).toEqual(['Required']);
    f.answer().value.set(' rest ');
    expect(f.answer().errors()).toEqual([]);
    expect(f().valid()).toBe(true);
  });
});
