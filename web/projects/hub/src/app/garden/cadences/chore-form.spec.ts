import { Component, input, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';

import type { Urgency } from 'fleet';
import { type ChoreDraft, EMPTY_CHORE_DRAFT } from '../core/cadence.domain';
import { choreAddRules, choreEditRules } from './chore.form';
import { FleetChoreForm } from './chore-form';

const DRAFT: ChoreDraft = { ...EMPTY_CHORE_DRAFT, key: 'nightly', routine: 'nightly' };
const URGENT = { id: 'u', slug: 'urgent', name: 'Urgent', default: false, retired: false } as Urgency;

@Component({
  selector: 'app-test-chore-form-host',
  imports: [FleetChoreForm],
  template: `<app-chore-form
    [form]="adding() ? addTree : editTree"
    [adding]="adding()"
    [urgencies]="urgencies()"
    [heldMode]="heldMode()"
    (save)="saves = saves + 1"
    (cancel)="cancels = cancels + 1"
  />`,
})
class Host {
  readonly adding = input(false);
  readonly urgencies = input<readonly Urgency[]>([]);
  readonly heldMode = input<string | null>(null);
  readonly draft = signal<ChoreDraft>(EMPTY_CHORE_DRAFT);
  readonly addTree = form(this.draft, choreAddRules);
  readonly editTree = form(this.draft, choreEditRules);
  saves = 0;
  cancels = 0;
}

describe('FleetChoreForm', () => {
  async function mount(inputs: { adding?: boolean; draft?: ChoreDraft; heldMode?: string | null } = {}) {
    await TestBed.configureTestingModule({
      imports: [Host],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    fixture.componentRef.setInput('adding', inputs.adding ?? false);
    fixture.componentRef.setInput('heldMode', inputs.heldMode ?? null);
    fixture.componentInstance.draft.set(inputs.draft ?? DRAFT);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const q = (suffix: string) => el.querySelector<HTMLElement>(`[data-testid="gardening-chore-form-${suffix}"]`);
    return { fixture, host: fixture.componentInstance, el, q };
  }

  it('binds the mode, promote, and key fields into the draft it is handed', async () => {
    const { fixture, host, q } = await mount({ adding: true, draft: { ...DRAFT, key: '' } });

    const key = q('key') as HTMLInputElement;
    key.value = 'weekly';
    key.dispatchEvent(new Event('input', { bubbles: true }));
    q('mode-delta')!.click();
    q('promote')!.click();
    await fixture.whenStable();

    expect(host.draft()).toMatchObject({ key: 'weekly', mode: 'delta', promote: false });
  });

  it('renders the key only when adding', async () => {
    expect((await mount({ adding: true })).q('key')).not.toBeNull();
    TestBed.resetTestingModule();
    expect((await mount({ adding: false })).q('key')).toBeNull();
  });

  it('offers a held mode this build does not know as itself, kept once another is picked', async () => {
    const { fixture, el, q } = await mount({ draft: { ...DRAFT, mode: 'sampled' }, heldMode: 'sampled' });

    const labels = () => Array.from(el.querySelectorAll('fleet-kit-option')).map((o) => o.textContent?.trim());
    expect(labels()).toEqual(['full', 'delta', 'sampled']);
    q('mode-delta')!.click();
    await fixture.whenStable();
    expect(labels()).toEqual(['full', 'delta', 'sampled']);
  });

  it('starts at the routine default and binds a selected urgency', async () => {
    const { fixture, host, q } = await mount();
    fixture.componentRef.setInput('urgencies', [URGENT]);
    await fixture.whenStable();
    const trigger = q('urgency')!;
    expect(trigger.textContent).toContain('Routine default');
    trigger.click();
    await fixture.whenStable();
    [...document.body.querySelectorAll<HTMLElement>('[role="option"]')].find((o) => o.textContent?.trim() === 'Urgent')!.click();
    await fixture.whenStable();
    expect(host.draft().urgency).toBe('urgent');
    fixture.destroy();
  });

  it('judges a blank routine required, with its field marked', async () => {
    const { fixture, host, q } = await mount({ draft: { ...DRAFT, routine: '' } });
    host.editTree.routine().markAsTouched();
    await fixture.whenStable();
    expect(host.editTree.routine().errors().map((e) => e.kind)).toEqual(['required']);
    expect(q('routine')!.closest('fleet-kit-field')!.querySelector('.req')).not.toBeNull();
  });

  it('emits save and cancel', async () => {
    const { fixture, host, q } = await mount();
    q('save')!.click();
    q('cancel')!.click();
    await fixture.whenStable();
    expect(host.saves).toBe(1);
    expect(host.cancels).toBe(1);
  });
});
