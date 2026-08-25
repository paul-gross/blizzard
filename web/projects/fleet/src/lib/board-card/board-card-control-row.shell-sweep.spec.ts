import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';

/**
 * The board card's control row genuinely stays a row (D8, issue #364) — a real
 * layout claim jsdom cannot make: it never actually lays out the flex row
 * `board-card.css`'s `.card-controls` declares, so `web:unit-test` cannot see
 * PROMOTE and DELETE collapse into an overlapping or overflowing pair at the
 * board right rail's own narrow width. Mounts a `not_ready` card with
 * `canControl` true — the one status both controls render on together, and so
 * the denser, more-likely-to-overflow case than `ready`'s DELETE-alone row.
 *
 * Follows `local-panel-mobile.shell-sweep.spec.ts`'s own multi-width shape (a
 * plain `for` loop over {@link WIDTHS}, one `it` per width) rather than a
 * parameterized-test helper this codebase does not otherwise use.
 *
 * Proven able to fail by setting `.card-controls`'s `flex-direction` to `column`
 * in `board-card.css`: `.card-promote`'s own `align-self: flex-start` happens to
 * keep the two controls' bounding rects from overlapping horizontally even
 * stacked, which is why this spec pins their shared `top` first — a bare
 * left/right-edge check alone passed vacuously against that exact regression.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const CARD: BoardCard = {
  chunkId: 'ch_01controlrow000000000000000',
  shortId: 'C-CROW',
  status: 'not_ready',
  node: 'build',
  nodeId: 'nd_build',
  pointerLabels: [],
  costUsd: 0,
  costPartial: false,
  completedAt: null,
};

// 390 (a typical phone) and 320 (the narrowest common phone) — the board right
// rail's own narrow-width ceiling, the same pair `local-panel-mobile.shell-sweep.spec.ts`
// sweeps for the runner's mobile chunk list.
const WIDTHS = [390, 320];

async function renderCard(width: number): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', CARD);
  fixture.componentRef.setInput('canControl', true);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return root;
}

describe('board-card control row shell sweep (web:shell-sweep, issue #364)', () => {
  for (const width of WIDTHS) {
    it(`keeps PROMOTE and DELETE side by side, non-overlapping and within the card, at width ${width}`, async () => {
      const root = await renderCard(width);
      try {
        const promote = root.querySelector<HTMLElement>('[data-testid="promote-chunk"]');
        const del = root.querySelector<HTMLElement>('[data-testid="delete-chunk"]');
        expect(promote, `width ${width}: fixture defect — PROMOTE did not render`).not.toBeNull();
        expect(del, `width ${width}: fixture defect — DELETE did not render`).not.toBeNull();

        const promoteRect = promote!.getBoundingClientRect();
        const delRect = del!.getBoundingClientRect();

        // Genuinely a row, not a column: two items sharing a row share a `top` (a
        // `flex-direction: column` regression instead stacks them at distinct `top`s,
        // however their cross-axis alignment happens to land — the failure mode a bare
        // left/right-edge check alone would miss).
        expect(
          delRect.top,
          `width ${width}: DELETE's top (${delRect.top}) differs from PROMOTE's (${promoteRect.top}) — the row collapsed to a column`,
        ).toBeCloseTo(promoteRect.top, 0);

        // Side by side, not overlapping: the two share a row, so DELETE's left edge sits
        // at or past PROMOTE's right edge.
        expect(
          delRect.left,
          `width ${width}: DELETE's left (${delRect.left}) sits before PROMOTE's right (${promoteRect.right}) — the row collapsed to a stack or an overlap`,
        ).toBeGreaterThanOrEqual(promoteRect.right);

        // Non-overlapping: the two rects share no horizontal span.
        const overlaps = promoteRect.left < delRect.right && delRect.left < promoteRect.right;
        expect(overlaps, `width ${width}: PROMOTE and DELETE overlap`).toBe(false);

        // Within the card: DELETE's right edge does not push past the card's own — the
        // failure mode a control row that overflows its narrow-width card produces.
        const card = root.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
        const cardRect = card.getBoundingClientRect();
        expect(
          delRect.right,
          `width ${width}: DELETE's right edge (${delRect.right}) overflows the card's own (${cardRect.right})`,
        ).toBeLessThanOrEqual(cardRect.right + 0.5);
      } finally {
        root.remove();
      }
    });
  }
});
