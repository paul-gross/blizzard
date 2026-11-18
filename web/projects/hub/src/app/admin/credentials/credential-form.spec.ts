import { Component, computed, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { CredentialForm } from './credential-form';
import { type CredentialDraft } from './credential.domain';
import { credentialRules } from './credential.form';
import { credentialEditChanges } from './credentials.model';
import { toEditBaseline, toEditRequest } from './credential.translate';

const bindings = [{ name: 'github.app-installation', forgeKind: 'github', settings: [
  { key: 'app_id', type: { kind: 'positive-integer' as const } },
  { key: 'installation_id', type: { kind: 'positive-integer' as const } },
] }];
const baseline = toEditBaseline({ id: 'id', revision: 1, slug: 's', name: '', forgeId: 'f', forgeSlug: 'f', forgeName: '', binding: 'github.app-installation', secretName: 'secret', settings: { app_id: 12, installation_id: 34 }, authority: '', retired: false, createdBy: '', createdByName: null, createdAt: '' }, bindings);

@Component({
  imports: [CredentialForm],
  template: `<app-credential-form heading="Edit" mode="edit" [form]="f" [fields]="[]" [baseline]="baseline.draft"
    [presentationDiff]="changes()" submitLabel="Save" testidPrefix="credential" />`,
})
class EditHost {
  readonly baseline = baseline;
  readonly draft = signal<CredentialDraft>({ ...baseline.draft, settings: [] });
  readonly f = form(this.draft, credentialRules);
  readonly changes = computed(() => credentialEditChanges(this.baseline, this.draft()));
}

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

  it('clears numeric settings through the controls and sends the replacement settings', async () => {
    await TestBed.configureTestingModule({ imports: [EditHost], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(EditHost);
    fixture.componentInstance.f().reset(structuredClone(baseline.draft));
    document.body.appendChild(fixture.nativeElement);
    await fixture.whenStable();
    for (const key of ['app_id', 'installation_id']) {
      const input = document.querySelector<HTMLInputElement>(`[data-testid="credential-setting-github.app-installation-${key}"]`)!;
      input.value = '';
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await fixture.whenStable();
    }
    expect(fixture.componentInstance.draft().settings.map((s) => s.text)).toEqual(['', '']);
    expect(fixture.componentInstance.changes().map((c) => c.field)).toEqual(['app_id', 'installation_id']);
    expect(toEditRequest(baseline, fixture.componentInstance.draft())).toEqual({ ok: true, request: { settings: {} } });
  });

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
