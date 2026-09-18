import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';

/**
 * The blocked marking's adjacency to the card's status (issue #461) — a real layout
 * claim jsdom cannot make: it never actually lays out `board-card.css`'s flex row, so
 * `web:unit-test` cannot see the marking overlap the status, wrap away from it, or push
 * past the card's own edge. The marking renders inside the status row, immediately after
 * the status it qualifies — this sweeps that it lands there without moving the status
 * itself or overflowing the card, at a wide width (800px — wider than any real board
 * column, a generous upper bound) and at 390px/320px (`bzh:narrow-viewport-tier-rule`).
 *
 * Shaped as a plain `for` loop over {@link WIDTHS}, one `it` per width, the way every
 * other sweep in the roster is, rather than a parameterized-test helper this codebase
 * does not otherwise use.
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
  blockedCount: 0,
  blockedOnStatus: null,
};

// 800 (wider than any real board column) and 390/320 (the phone pair
// `local-panel-mobile.shell-sweep.spec.ts` already sweeps) — not a board column's own
// narrower fractional share of either.
const WIDTHS = [800, 390, 320];

async function renderCard(width: number, blockedOn: string | null, blockedCount = blockedOn ? 1 : 0): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', { ...BASE, blockedOn, blockedCount });
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return root;
}

describe('board card blocked marking adjacency shell sweep (web:shell-sweep, issue #461)', () => {
  for (const width of WIDTHS) {
    it(`renders the marking beside the status, without displacing or overflowing it, at width ${width}`, async () => {
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
        const marking = root.querySelector<HTMLElement>('[data-testid="chunk-blocked-by"]');
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

        // Beside, not overlapping and not wrapped away: the marking shares the status's
        // own line and starts after it ends.
        expect(
          markingRect.left,
          `width ${width}: the marking (left ${markingRect.left}) does not start after the status ends (right ${statusRect.right})`,
        ).toBeGreaterThanOrEqual(statusRect.right - 0.5);
        expect(
          markingRect.top,
          `width ${width}: the marking wrapped off the status's line (top ${markingRect.top} vs ${statusRect.top})`,
        ).toBeLessThan(statusRect.bottom);

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

describe('board card blocked-by count shell sweep (web:shell-sweep)', () => {
  it('keeps a multi-prerequisite count on the status row without overflowing the card at 320px', async () => {
    const root = await renderCard(320, 'ch_01prereq00000000000000000', 4);
    try {
      const status = root.querySelector<HTMLElement>('[data-testid="chunk-status"]')!;
      const marking = root.querySelector<HTMLElement>('[data-testid="chunk-blocked-by"]')!;
      const cardRect = root.querySelector<HTMLElement>('[data-testid="chunk-card"]')!.getBoundingClientRect();

      expect(marking.textContent).toContain('4 chunks');
      expect(
        marking.getBoundingClientRect().top,
        'the count wrapped off the status line',
      ).toBeLessThan(status.getBoundingClientRect().bottom);
      expect(
        marking.getBoundingClientRect().right,
        "the count's right edge overflows the card",
      ).toBeLessThanOrEqual(cardRect.right + 0.5);
    } finally {
      root.remove();
    }
  });
});
