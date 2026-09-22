import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';

/**
 * The card's right-hand meta group at its fullest — a done-lane card carrying its
 * completion stamp, a billed cost, and a cost estimate side by side, each
 * `white-space: nowrap` — a real layout claim jsdom cannot make: it never lays out
 * `board-card.css`'s flex row, so `web:unit-test` cannot see the three figures overlap
 * one another, wrap off their shared line, or push past the card's own edge. Swept at
 * 800px (wider than any real board column) and at 390px/320px
 * (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const CARD: BoardCard = {
  chunkId: 'ch_01costcard00000000000000000',
  shortId: 'C-COST',
  status: 'done',
  node: 'deliver',
  nodeId: 'nd_deliver',
  pointerLabels: [],
  costUsd: 123.45,
  costPartial: true,
  estimatedCostUsd: 678.9,
  completedAt: '2026-07-20T09:30:00Z',
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
};

const WIDTHS = [800, 390, 320];

async function renderCard(width: number): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', CARD);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return root;
}

describe('board card cost figures shell sweep (web:shell-sweep)', () => {
  for (const width of WIDTHS) {
    it(`keeps the done-at stamp, billed cost, and estimate on one line inside the card at width ${width}`, async () => {
      const root = await renderCard(width);
      try {
        const ids = ['chunk-done-at', 'card-cost', 'card-cost-estimate'];
        const rects = ids.map((id) => {
          const el = root.querySelector<HTMLElement>(`[data-testid="${id}"]`);
          expect(el, `width ${width}: fixture defect — ${id} did not render`).not.toBeNull();
          return el!.getBoundingClientRect();
        });
        const cardRect = root.querySelector<HTMLElement>('[data-testid="chunk-card"]')!.getBoundingClientRect();

        for (let i = 0; i < rects.length; i++) {
          expect(
            rects[i].right,
            `width ${width}: ${ids[i]}'s right edge (${rects[i].right}) overflows the card's own (${cardRect.right})`,
          ).toBeLessThanOrEqual(cardRect.right + 0.5);
        }
        for (let i = 1; i < rects.length; i++) {
          // Side by side, never overlapping: each figure starts after the previous one ends...
          expect(
            rects[i].left,
            `width ${width}: ${ids[i]} (left ${rects[i].left}) overlaps ${ids[i - 1]} (right ${rects[i - 1].right})`,
          ).toBeGreaterThanOrEqual(rects[i - 1].right - 0.5);
          // ...and on the same line, never wrapped below it.
          expect(
            rects[i].top,
            `width ${width}: ${ids[i]} wrapped off ${ids[i - 1]}'s line (top ${rects[i].top})`,
          ).toBeLessThan(rects[i - 1].bottom);
        }
      } finally {
        root.remove();
      }
    });
  }
});
