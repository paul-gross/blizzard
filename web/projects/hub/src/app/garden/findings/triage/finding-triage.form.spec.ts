import { Injector, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { type FindingTriageVerb } from '../../core/finding-triage-verb.domain';
import { emptyTriageDraft, type FindingTriageDraft } from './finding-triage.domain';
import { findingTriageRules } from './finding-triage.form';

const VERBS: readonly FindingTriageVerb[] = [
  'resolve',
  'confirm-gone',
  'wont-fix',
  'not-a-finding',
  'supersede',
  'reopen',
];

function formFor(draft: FindingTriageDraft) {
  return form(signal(draft), findingTriageRules, { injector: TestBed.inject(Injector) });
}

describe('findingTriageRules', () => {
  for (const verb of VERBS) {
    describe(verb, () => {
      it('requires a note, counts whitespace as blank, and marks the field required', () => {
        const f = formFor(emptyTriageDraft(verb));
        expect(f.note().required()).toBe(true);
        expect(f.note().errors().map((e) => e.message)).toEqual(['Required']);

        f.note().value.set('   ');
        expect(f.note().errors().map((e) => e.message)).toEqual(['Required']);

        f.note().value.set('a note');
        expect(f.note().errors()).toEqual([]);
      });

      if (verb === 'supersede') {
        it('requires the absorbing finding id, whitespace counting as blank', () => {
          const f = formFor({ ...emptyTriageDraft(verb), note: 'n' });
          expect(f.supersededBy().required()).toBe(true);
          expect(f.supersededBy().errors().map((e) => e.message)).toEqual(['Required']);
          expect(f().valid()).toBe(false);

          f.supersededBy().value.set('  ');
          expect(f.supersededBy().errors().map((e) => e.message)).toEqual(['Required']);

          f.supersededBy().value.set('fnd_9');
          expect(f.supersededBy().errors()).toEqual([]);
          expect(f().valid()).toBe(true);
        });
      } else {
        it('neither requires nor judges the absorbing finding id', () => {
          const f = formFor({ ...emptyTriageDraft(verb), note: 'n' });
          expect(f.supersededBy().required()).toBe(false);
          expect(f.supersededBy().errors()).toEqual([]);
          expect(f().valid()).toBe(true);
        });
      }
    });
  }
});
