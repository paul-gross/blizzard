import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { EMPTY_CREDENTIAL_DRAFT, type CredentialDraft, withForge } from './credential.domain';
import { credentialRules } from './credential.form';

function tree(draft: CredentialDraft) {
  TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
  return TestBed.runInInjectionContext(() => form(signal(draft), credentialRules));
}
describe('credential rules', () => {
  afterEach(() => TestBed.resetTestingModule());
  it('requires fixed fields and not name', () => {
    const f = tree(EMPTY_CREDENTIAL_DRAFT);
    for (const key of ['slug', 'forge', 'binding', 'secret'] as const) expect(f[key]().required()).toBe(true);
    expect(f.name().required()).toBe(false);
  });
  it('hides and does not validate inactive entries', () => {
    const f = tree({ slug: 's', name: '', forge: 'f', binding: 'a', secret: 's', settings: [
      { binding: 'a', key: 'n', type: { kind: 'positive-integer' }, text: '' },
      { binding: 'b', key: 'n', type: { kind: 'positive-integer' }, text: 'bad' },
    ] });
    expect(f.settings[1].text().hidden()).toBe(true);
    expect(f().valid()).toBe(true);
    f.settings[0].text().value.set('0');
    expect(f().valid()).toBe(false);
  });
  it('clears incompatible binding without discarding settings', () => {
    const draft = { ...EMPTY_CREDENTIAL_DRAFT, forge: 'a', binding: 'token' };
    expect(withForge(draft, 'b', [{ forgeId: 'b', bindings: ['app'] }]).binding).toBe('');
  });
});
