import { Component, computed, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import { CredentialForm } from './credential-form';
import { credentialRules } from './credential.form';
import { credentialEditChanges } from './credentials.model';
import { toEditBaseline, toEditRequest } from './credential.translate';

const bindings = [{ name: 'github.token', forgeKind: 'github', settings: [
  { key: 'variant', type: { kind: 'choice' as const, choices: ['classic', 'fine-grained'] } },
] }];
const baseline = toEditBaseline({ id: 'id', revision: 1, slug: 's', name: '', forgeId: 'f', forgeSlug: 'f', forgeName: '', binding: 'github.token', secretName: 'secret', settings: { variant: 'classic', other: 'preserved' }, authority: '', retired: false, createdBy: '', createdByName: null, createdAt: '' }, bindings);

@Component({
  imports: [CredentialForm],
  template: `<app-credential-form heading="Edit" mode="edit" [form]="f" [fields]="[]" [baseline]="baseline.draft"
    [presentationDiff]="changes()" submitLabel="Save" testidPrefix="credential" />`,
})
class Host {
  readonly baseline = baseline;
  readonly draft = signal(structuredClone(baseline.draft));
  readonly f = form(this.draft, credentialRules);
  readonly changes = computed(() => credentialEditChanges(this.baseline, this.draft()));
}

describe('CredentialForm choice settings', () => {
  afterEach(() => { document.body.innerHTML = ''; TestBed.resetTestingModule(); });

  it('clears a selected optional choice and sends replacement settings', async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    document.body.appendChild(fixture.nativeElement);
    await fixture.whenStable();
    const trigger = document.querySelector<HTMLElement>('[data-testid="credential-setting-github.token-variant"]')!;
    expect(trigger.textContent).toContain('classic');
    trigger.click();
    await fixture.whenStable();
    const options = Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'));
    expect(options.map((option) => option.textContent?.trim())).toEqual(['Not set', 'classic', 'fine-grained']);
    options[0].click();
    await fixture.whenStable();
    expect(trigger.textContent).toContain('Not set');
    expect(fixture.componentInstance.draft().settings[0].text).toBe('');
    expect(fixture.componentInstance.changes().map((change) => change.field)).toContain('variant');
    expect(toEditRequest(baseline, fixture.componentInstance.draft())).toEqual({ ok: true, request: { settings: { other: 'preserved' } } });
  });
});
