import { Component, input, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { page, userEvent } from 'vitest/browser';

import { GardeningProposalAcceptDialogView } from './gardening-proposal-accept-dialog-view';
import { acceptanceDraftFor } from './proposal-acceptance.domain';
import { proposalAcceptanceRules } from './proposal-acceptance.form';

/** Mounts the view over a real `form()`, as the container does. */
@Component({
  selector: 'app-accept-sweep-host',
  imports: [GardeningProposalAcceptDialogView],
  template: `<app-gardening-proposal-accept-dialog-view
    [proposalTitle]="title()"
    [form]="f"
    [urgencyOptions]="[{ value: '', label: 'Tenant default (Normal)' }, { value: 'urgent', label: 'Urgent' }]"
  />`,
})
class Host {
  readonly title = input.required<string>();
  readonly f = form(signal(acceptanceDraftFor('Seventeen modules narrate their own change history.')), proposalAcceptanceRules);
}

/**
 * The Accept dialog's own half of `web:shell-sweep` — the mint/decline option group genuinely stacks its two options,
 * the `KitForm` action row stays inside the panel, and the reason field sits below the options in both modes, at the
 * phone and desktop widths the dialog is reachable at — real CSS layout claims jsdom cannot make.
 *
 * Mounts `GardeningProposalAcceptDialogView` over a real `form()` — no query double, matching how the container
 * actually feeds it (`bzh:frontend-container-presentational`).
 *
 * Excluded from the default `ng test hub` run — run it via `npm run shell-sweep` (`web/scripts/shell-sweep.js`).
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

const rect = (root: HTMLElement, testid: string) =>
  root.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!.getBoundingClientRect();

describe('GardeningProposalAcceptDialogView shell sweep (web:shell-sweep)', () => {
  for (const width of [320, 390, 1024]) {
    it(`stacks the options, keeps the action row inside the panel, and the reason below the options at ${width}px`, async () => {
      const { root, fixture } = await mount(width);
      try {
        for (const mode of ['mint', 'decline'] as const) {
          await userEvent.click(root.querySelector(`[data-testid="proposal-accept-mode-${mode}"]`)!);
          await fixture.whenStable();

          const mint = rect(root, 'proposal-accept-mode-mint');
          const decline = rect(root, 'proposal-accept-mode-decline');
          expect(decline.top, 'decline option did not land below mint').toBeGreaterThanOrEqual(mint.bottom);

          const modeField = rect(root, 'proposal-accept-mode-field');
          const reasonField = rect(root, 'proposal-accept-reason-field');
          expect(reasonField.top, `the reason field did not land below the options (${mode})`).toBeGreaterThanOrEqual(
            modeField.bottom,
          );

          const cancel = rect(root, 'kit-form-cancel');
          const submit = rect(root, 'kit-form-save');
          expect(Math.abs(cancel.top - submit.top), 'Cancel and Accept must sit on the same row').toBeLessThan(2);
          const [first, second] = cancel.left < submit.left ? [cancel, submit] : [submit, cancel];
          expect(second.left, 'the action buttons must not overlap').toBeGreaterThanOrEqual(first.right);

          const panel = rect(root, 'gardening-proposal-accept-dialog');
          expect(first.left, 'the action row must not overflow the panel').toBeGreaterThanOrEqual(panel.left - 1);
          expect(second.right, 'the action row must not overflow the panel').toBeLessThanOrEqual(panel.right + 1);
        }
      } finally {
        root.remove();
      }
    });
  }
});
