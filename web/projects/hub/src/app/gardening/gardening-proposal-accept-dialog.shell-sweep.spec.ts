import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page, userEvent } from 'vitest/browser';

import { GardeningProposalAcceptDialogView } from './gardening-proposal-accept-dialog-view';

/**
 * The Accept dialog's own half of `web:shell-sweep` — the
 * mint/decline radiogroup genuinely stacks its two options, the footer's
 * Cancel/Accept buttons genuinely sit side by side, and neither overflows the panel,
 * at the phone and desktop widths the dialog is reachable at — real CSS layout claims
 * jsdom cannot make.
 *
 * Mounts `GardeningProposalAcceptDialogView` directly with plain inputs — no query
 * double, matching how the container actually feeds it
 * (`bzh:frontend-container-presentational`).
 *
 * Excluded from the default `ng test hub` run — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
async function mount(width: number) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [GardeningProposalAcceptDialogView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(GardeningProposalAcceptDialogView);
  fixture.componentRef.setInput('proposalId', 'gp_1');
  fixture.componentRef.setInput('proposalTitle', 'Author a docstring standard covering change-history narration');
  fixture.componentRef.setInput('proposalBody', 'Seventeen modules narrate their own change history.');
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 800);
  await fixture.whenStable();
  return { fixture, root };
}

describe('GardeningProposalAcceptDialogView shell sweep (web:shell-sweep)', () => {
  for (const width of [390, 1024]) {
    it(`stacks the mode options and sits Cancel/Accept side by side at ${width}px`, async () => {
      const { root } = await mount(width);
      try {
        const mint = root.querySelector<HTMLElement>('[data-testid="proposal-accept-mode-mint"]')!;
        const decline = root.querySelector<HTMLElement>('[data-testid="proposal-accept-mode-decline"]')!;
        expect(decline.getBoundingClientRect().top, 'decline option did not land below mint').toBeGreaterThanOrEqual(
          mint.getBoundingClientRect().bottom,
        );

        const cancel = root.querySelector<HTMLElement>('[data-testid="proposal-accept-dialog-cancel"]')!;
        const submit = root.querySelector<HTMLElement>('[data-testid="proposal-accept-dialog-submit"]')!;
        const cancelRect = cancel.getBoundingClientRect();
        const submitRect = submit.getBoundingClientRect();
        expect(
          Math.abs(cancelRect.top - submitRect.top),
          'Cancel and Accept must sit on the same row, not stacked',
        ).toBeLessThan(2);
        expect(submitRect.left, 'Accept must not overlap Cancel').toBeGreaterThanOrEqual(cancelRect.right);

        const panel = root.querySelector<HTMLElement>('[data-testid="gardening-proposal-accept-dialog"]')!;
        const panelRect = panel.getBoundingClientRect();
        expect(submitRect.right, 'Accept must not overflow the dialog panel').toBeLessThanOrEqual(
          panelRect.right + 1,
        );
      } finally {
        root.remove();
      }
    });
  }

  it('shows the decline reason field below the mode options once decline is selected', async () => {
    const { root, fixture } = await mount(1024);
    try {
      await userEvent.click(root.querySelector('[data-testid="proposal-accept-mode-decline"]')!);
      await fixture.whenStable();

      const modeField = root.querySelector<HTMLElement>('[data-testid="proposal-accept-mode-field"]')!;
      const reasonField = root.querySelector<HTMLElement>('[data-testid="proposal-accept-decline-reason-field"]')!;
      expect(
        reasonField.getBoundingClientRect().top,
        'the decline reason field did not land below the mode field',
      ).toBeGreaterThanOrEqual(modeField.getBoundingClientRect().bottom);
    } finally {
      root.remove();
    }
  });
});
