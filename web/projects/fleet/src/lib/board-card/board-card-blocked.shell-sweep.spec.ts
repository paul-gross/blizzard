import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';

/**
 * The blocked marking's adjacency to the card's status (issue #461) — a real layout
 * claim jsdom cannot make: it never actually lays out `board-card.css`'s flex column,
 * so `web:unit-test` cannot see the marking overlap the status row or push past the
 * card's own edge. `ChunkBlocked` renders outside `.card-open` (the whole open button
 * cannot host a nested interactive element), directly under the status row it marks —
 * this sweeps that it lands there without moving the status itself or overflowing the
 * card, at a wide width (800px — wider than any real board column, a generous upper
 * bound) and at 390px/320px (`bzh:narrow-viewport-tier-rule`).
 *
 * Follows `board-card-control-row.shell-sweep.spec.ts`'s own shape (a plain `for` loop
 * over {@link WIDTHS}, one `it` per width) rather than a parameterized-test helper this
 * codebase does not otherwise use.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const BASE: BoardCard = {
  chunkId: 'ch_01blockedcard0000000000000',
  shortId: 'C-BLKD',
  status: 'ready',
  node: 'build',
  nodeId: 'nd_build',
  pointerLabels: [],
  costUsd: 0,
  costPartial: false,
  completedAt: null,
  blockedOn: null,
};

// 800 (wider than any real board column) and 390/320 (the phone pair
// `board-card-control-row.shell-sweep.spec.ts` and `local-panel-mobile.shell-sweep.spec.ts`
// already sweep) — not a board column's own narrower fractional share of either.
const WIDTHS = [800, 390, 320];

async function renderCard(width: number, blockedOn: string | null): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', { ...BASE, blockedOn });
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return root;
}

describe('board card blocked marking adjacency shell sweep (web:shell-sweep, issue #461)', () => {
  for (const width of WIDTHS) {
    it(`renders the marking adjacent to the status, without displacing or overflowing it, at width ${width}`, async () => {
      const plainRoot = await renderCard(width, null);
      let statusRectBefore: DOMRect;
      try {
        const status = plainRoot.querySelector<HTMLElement>('[data-testid="chunk-status"]');
        expect(status, `width ${width}: fixture defect — status did not render`).not.toBeNull();
        statusRectBefore = status!.getBoundingClientRect();
      } finally {
        plainRoot.remove();
      }

      const root = await renderCard(width, 'ch_01prereq00000000000000000');
      try {
        const status = root.querySelector<HTMLElement>('[data-testid="chunk-status"]');
        const marking = root.querySelector<HTMLElement>('[data-testid="chunk-blocked"]');
        expect(status, `width ${width}: fixture defect — status did not render`).not.toBeNull();
        expect(marking, `width ${width}: fixture defect — blocked marking did not render`).not.toBeNull();

        const statusRect = status!.getBoundingClientRect();
        const markingRect = marking!.getBoundingClientRect();
        const cardRect = root.querySelector<HTMLElement>('[data-testid="chunk-card"]')!.getBoundingClientRect();

        // Not displaced: the status renders at the same position whether or not the
        // card is blocked — the marking never pushes it.
        expect(
          statusRect.top,
          `width ${width}: the marking displaced the status's top (${statusRect.top} vs ${statusRectBefore.top})`,
        ).toBeCloseTo(statusRectBefore.top, 0);
        expect(
          statusRect.left,
          `width ${width}: the marking displaced the status's left (${statusRect.left} vs ${statusRectBefore.left})`,
        ).toBeCloseTo(statusRectBefore.left, 0);

        // Adjacent, not overlapping: the marking sits below the status row.
        const overlaps =
          statusRect.top < markingRect.bottom &&
          markingRect.top < statusRect.bottom &&
          statusRect.left < markingRect.right &&
          markingRect.left < statusRect.right;
        expect(overlaps, `width ${width}: the marking overlaps the status`).toBe(false);
        expect(
          markingRect.top,
          `width ${width}: the marking (top ${markingRect.top}) does not sit below the status (bottom ${statusRect.bottom})`,
        ).toBeGreaterThanOrEqual(statusRect.bottom - 0.5);

        // Within the card: the marking's own right edge does not push past the card's.
        expect(
          markingRect.right,
          `width ${width}: the marking's right edge (${markingRect.right}) overflows the card's own (${cardRect.right})`,
        ).toBeLessThanOrEqual(cardRect.right + 0.5);
      } finally {
        root.remove();
      }
    });
  }
});
