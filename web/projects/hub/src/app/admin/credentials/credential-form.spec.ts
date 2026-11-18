import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { CredentialForm } from './credential-form';
import { type CredentialDraft } from './credential.domain';
import { credentialRules } from './credential.form';

@Component({
  imports: [CredentialForm],
  template: `<app-credential-form heading="Create" mode="create" [form]="f" [fields]="[]"
    submitLabel="Create" testidPrefix="credential" (save)="saved = true" />`,
})
class Host {
  readonly f = form(signal<CredentialDraft>({
    slug: 's', name: '', forge: 'f', binding: 'a', secret: 'secret-name', settings: [
      { binding: 'a', key: 'count', type: { kind: 'positive-integer' }, text: '' },
      { binding: 'a', key: 'kind', type: { kind: 'choice', choices: ['one', 'two'] }, text: '' },
      { binding: 'b', key: 'other', type: { kind: 'unknown', raw: 'future' }, text: '' },
    ],
  }), credentialRules);
  saved = false;
}

describe('CredentialForm', () => {
  afterEach(() => { document.body.innerHTML = ''; TestBed.resetTestingModule(); });

  it('projects typed settings into the form, validates them and switches active binding', async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    document.body.appendChild(fixture.nativeElement);
    await fixture.whenStable();
    const find = (key: string) => document.querySelector(`[data-testid="credential-setting-${key}"]`);
    expect(find('a-count')).not.toBeNull();
    expect(find('a-kind')).not.toBeNull();
    expect(find('b-other')).toBeNull();
    fixture.componentInstance.f.settings[0].text().value.set('0');
    expect(fixture.componentInstance.f().valid()).toBe(false);
    fixture.componentInstance.f.binding().value.set('b');
    await fixture.whenStable();
    expect(find('a-count')).toBeNull();
    expect(find('b-other')).not.toBeNull();
    expect(fixture.componentInstance.f().valid()).toBe(true);
    (document.querySelector('[data-testid="kit-form-save"]') as HTMLElement).click();
    await fixture.whenStable();
    expect(fixture.componentInstance.saved).toBe(true);
  });
});
