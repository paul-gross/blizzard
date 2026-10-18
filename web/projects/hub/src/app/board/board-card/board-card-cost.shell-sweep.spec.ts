import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';

/**
 * The card's cost row at its fullest — a done-lane card's completion stamp at its left, the
 * cost figure flush with the card's right edge — sits below the step row and never
 * overlaps, wraps, or overflows the card, at 800px and at 390px/320px
 * (`bzh:narrow-viewport-tier-rule`). Under a real browser — see `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep.
 */
const CARD: BoardCard = {
  chunkId: 'ch_01costcard00000000000000000',
  shortId: 'C-COST',
  status: 'done',
  terminal: true,
  promotable: false,
  node: 'deliver',
  nodeId: 'nd_deliver',
  workLines: [],
  itemsRead: true,
  costUsd: 123.45,
  costPartial: true,
  estimatedCostUsd: 678.9,
  completedAt: '2026-07-20T09:30:00Z',
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
};

const WIDTHS = [800, 390, 320];

async function renderCard(width: number, card: BoardCard = CARD): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', card);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return root;
}

describe('board card cost figures shell sweep (web:shell-sweep)', () => {
  for (const width of WIDTHS) {
    it(`keeps the done-at stamp and the cost figure on one line inside the card at width ${width}`, async () => {
      const root = await renderCard(width);
      try {
        const ids = ['chunk-done-at', 'card-cost'];
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

  const COST_ROW_CASES: readonly { label: string; card: BoardCard; stamp: boolean; cost: boolean }[] = [
    { label: 'the stamp alone', card: { ...CARD, costUsd: 0, estimatedCostUsd: null, costPartial: false }, stamp: true, cost: false },
    { label: 'the cost alone', card: { ...CARD, status: 'running', terminal: false, completedAt: null }, stamp: false, cost: true },
    { label: 'both', card: CARD, stamp: true, cost: true },
  ];
  for (const { label, card, stamp, cost } of COST_ROW_CASES) {
    it(`sits the cost row below the step row, the stamp at its left and the cost at its end, with ${label}`, async () => {
      const root = await renderCard(390, card);
      try {
        const row = root.querySelector<HTMLElement>('[data-testid="card-cost-row"]');
        expect(row, 'fixture defect — the cost row did not render').not.toBeNull();
        const rowRect = row!.getBoundingClientRect();
        if (stamp) {
          const at = root.querySelector<HTMLElement>('[data-testid="chunk-done-at"]')!.getBoundingClientRect();
          expect(Math.abs(at.left - rowRect.left), 'the stamp does not hold the row\'s left').toBeLessThanOrEqual(0.5);
        }
        if (cost) {
          const figure = root.querySelector<HTMLElement>('[data-testid="card-cost"]')!.getBoundingClientRect();
          expect(Math.abs(figure.right - rowRect.right), 'the cost is not flush with the row\'s end').toBeLessThanOrEqual(0.5);
        }
        const id = root.querySelector<HTMLElement>('[data-testid="chunk-id"]')!.getBoundingClientRect();
        expect(rowRect.top, 'the cost row is not below the step row').toBeGreaterThanOrEqual(id.bottom - 0.5);
      } finally {
        root.remove();
      }
    });
  }
});
