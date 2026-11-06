import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { type ChoreDraft, FleetChoreForm } from './chore-form';

const DRAFT: ChoreDraft = { key: 'nightly', routine: 'nightly', scopes: 'all', mode: 'full', promote: true, note: '' };

describe('FleetChoreForm', () => {
  async function mount(initial: ChoreDraft) {
    await TestBed.configureTestingModule({
      imports: [FleetChoreForm],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(FleetChoreForm);
    fixture.componentRef.setInput('initial', initial);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const saved: ChoreDraft[] = [];
    fixture.componentInstance.saved.subscribe((draft) => saved.push(draft));
    return { fixture, el, saved };
  }

  it('emits the draft with the mode and promote picked', async () => {
    const { fixture, el, saved } = await mount(DRAFT);

    const delta = Array.from(el.querySelectorAll<HTMLButtonElement>('fleet-kit-chips button')).find(
      (b) => b.textContent?.trim() === 'delta',
    )!;
    delta.click();
    const promote = el.querySelector<HTMLInputElement>('[data-testid="gardening-chore-form-promote"]')!;
    promote.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-chore-form-save"]')!.click();

    expect(saved).toEqual([{ ...DRAFT, mode: 'delta', promote: false }]);
  });

  it('offers a mode this build does not know as itself, and keeps it', async () => {
    const { el, saved } = await mount({ ...DRAFT, mode: 'sampled' });

    const labels = Array.from(el.querySelectorAll('fleet-kit-chips button')).map((b) => b.textContent?.trim());
    expect(labels).toEqual(['full', 'delta', 'sampled']);
    el.querySelector<HTMLButtonElement>('[data-testid="gardening-chore-form-save"]')!.click();
    expect(saved[0].mode).toBe('sampled');
  });
});
