import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { repositoryDraft } from './repository.builders';
import { EMPTY_REPOSITORY_DRAFT, PARK_TIMEOUT_MESSAGE, type RepositoryDraft } from './repository.domain';
import { repositoryRules } from './repository.form';

function tree(draft: RepositoryDraft, rules = repositoryRules) {
  TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
  return TestBed.runInInjectionContext(() => form(signal(draft), rules));
}

describe('repository rules', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('marks forge, credential and every other required field before anything is typed', () => {
    const f = tree(EMPTY_REPOSITORY_DRAFT);
    for (const key of ['slug', 'forge', 'owner', 'repo', 'baseBranch', 'credential'] as const) {
      expect(f[key]().required()).toBe(true);
      expect(f[key]().valid()).toBe(false);
    }
    expect(f.name().required()).toBe(false);
    expect(f.landingPolicy().required()).toBe(false);
  });

  it('passes a complete draft with a blank name and landing policy', () => {
    expect(tree(repositoryDraft({ landingPolicy: '' }))().valid()).toBe(true);
  });

  it('refuses a missing forge, and a missing credential, on their fields', () => {
    const noForge = tree(repositoryDraft({ forge: '' }));
    expect(noForge.forge().valid()).toBe(false);
    expect(noForge().valid()).toBe(false);
    TestBed.resetTestingModule();
    const noCredential = tree(repositoryDraft({ credential: '' }));
    expect(noCredential.credential().valid()).toBe(false);
    expect(noCredential().valid()).toBe(false);
  });

  it('keeps the slug required on edit, where it may be renamed', () => {
    expect(tree(repositoryDraft({ slug: '' }), repositoryRules).slug().valid()).toBe(false);
  });

  describe('park timeout', () => {
    const parked = (hours: string, parkTimeoutDefault = false) =>
      tree(repositoryDraft({ landingPolicy: 'park', parkTimeoutDefault, parkTimeoutHours: hours }));

    it('is hidden and unjudged unless the policy parks', () => {
      const f = tree(repositoryDraft({ parkTimeoutDefault: false, parkTimeoutHours: 'x' }));
      expect(f.parkTimeoutHours().hidden()).toBe(true);
      expect(f().valid()).toBe(true);
    });

    it('is disabled and unjudged while the default is chosen', () => {
      const f = parked('x', true);
      expect(f.parkTimeoutHours().hidden()).toBe(false);
      expect(f.parkTimeoutHours().disabled()).toBe(true);
      expect(f().valid()).toBe(true);
    });

    it('takes whole hours from 1', () => {
      expect(parked('1')().valid()).toBe(true);
      TestBed.resetTestingModule();
      expect(parked('48')().valid()).toBe(true);
    });

    it.each([
      ['blank', ''],
      ['malformed', '4.5'],
      ['signed', '-1'],
      ['zero', '0'],
      ['unsafe', '9007199254740992'],
      ['overflowing', '99999999999999999999'],
    ])('marks %s stated hours', (_name, hours) => {
      const f = parked(hours);
      expect(f.parkTimeoutHours().errors().map((e) => e.message)).toEqual([PARK_TIMEOUT_MESSAGE]);
      expect(f().valid()).toBe(false);
    });

    it('keeps the typed text while the default is chosen and judges it again when unchosen', () => {
      const f = parked('0', true);
      expect(f().valid()).toBe(true);
      f.parkTimeoutDefault().value.set(false);
      expect(f.parkTimeoutHours().value()).toBe('0');
      expect(f().valid()).toBe(false);
    });
  });
});
