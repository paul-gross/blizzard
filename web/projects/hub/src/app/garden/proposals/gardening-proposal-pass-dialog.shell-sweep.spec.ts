import { Component, input, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { page, userEvent } from 'vitest/browser';

import { GardeningProposalPassDialogView } from './gardening-proposal-pass-dialog-view';
import { emptyPassDraft } from './proposal-pass.domain';
import { proposalPassRules } from './proposal-pass.form';

/** Mounts the view over a real `form()`, as the container does. */
@Component({
  selector: 'app-pass-sweep-host',
  imports: [GardeningProposalPassDialogView],
  template: '<app-gardening-proposal-pass-dialog-view [proposalTitle]="title()" [form]="f" />',
})
class Host {
  readonly title = input.required<string>();
  readonly f = form(signal(emptyPassDraft()), proposalPassRules);
}

/**
 * The Pass dialog's own half of `web:shell-sweep` — the footer's
 * Cancel/Pass buttons genuinely sit side by side, neither overflowing the panel, at
 * the phone and desktop widths the dialog is reachable at — a real CSS layout claim
 * jsdom cannot make.
 *
 * Mounts `GardeningProposalPassDialogView` over a real `form()` — no query
 * double, matching how the container actually feeds it
 * (`bzh:frontend-container-presentational`).
 *
 * Excluded from the default `ng test hub` run — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
async function mount(width: number) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [Host],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(Host);
  fixture.componentRef.setInput('title', 'Author a docstring standard covering change-history narration');
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 800);
  await fixture.whenStable();
  return { fixture, root };
}

describe('GardeningProposalPassDialogView shell sweep (web:shell-sweep)', () => {
  for (const width of [390, 1024]) {
    it(`sits Cancel and Pass side by side with neither overflowing the panel at ${width}px`, async () => {
      const { root, fixture } = await mount(width);
      try {
        const reason = root.querySelector<HTMLTextAreaElement>('[data-testid="proposal-pass-reason-input"]')!;
        await userEvent.type(reason, 'not worth it yet');
        await fixture.whenStable();

        const cancel = root.querySelector<HTMLElement>('[data-testid="kit-form-cancel"]')!;
        const submit = root.querySelector<HTMLElement>('[data-testid="kit-form-save"]')!;
        const cancelRect = cancel.getBoundingClientRect();
        const submitRect = submit.getBoundingClientRect();
        expect(
          Math.abs(cancelRect.top - submitRect.top),
          'Cancel and Pass must sit on the same row, not stacked',
        ).toBeLessThan(2);
        const [first, second] = cancelRect.left < submitRect.left ? [cancelRect, submitRect] : [submitRect, cancelRect];
        expect(second.left, 'the action buttons must not overlap').toBeGreaterThanOrEqual(first.right);

        const panel = root.querySelector<HTMLElement>('[data-testid="gardening-proposal-pass-dialog"]')!;
        const panelRect = panel.getBoundingClientRect();
        expect(submitRect.right, 'Pass must not overflow the dialog panel').toBeLessThanOrEqual(panelRect.right + 1);
      } finally {
        root.remove();
      }
    });
  }
});
