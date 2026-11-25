import { Injector, provideZonelessChangeDetection, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { TestBed } from '@angular/core/testing';

import type { HubSettingDraft } from './hub-settings.domain';
import { hubSettingRules } from './hub-settings.form';

function edit(draft: HubSettingDraft, current: unknown) {
  TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
  return form(signal(draft), hubSettingRules(() => current), { injector: TestBed.inject(Injector) });
}

describe('hubSettingRules', () => {
  it('refuses text that does not read as a value for the key', () => {
    const f = edit({ value: 'no', useDefault: false }, 60);
    expect(f.value().errors().map((e) => e.message)).toEqual(['Enter a valid JSON value.']);
  });

  it('accepts any text for a string key', () => {
    expect(edit({ value: 'no', useDefault: false }, 'text').value().errors()).toEqual([]);
  });

  it('disables the value and judges nothing while the default is chosen', () => {
    const f = edit({ value: 'no', useDefault: true }, 60);
    expect(f.value().disabled()).toBe(true);
    expect(f().valid()).toBe(true);
  });
});
