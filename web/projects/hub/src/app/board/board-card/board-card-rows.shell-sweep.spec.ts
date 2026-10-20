import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { BoardCard } from './board-card';
import { BoardCardComponent } from './board-card';

/**
 * A card's rows stack in one order at their fullest — the title, the blocked marking, the
 * PR links, the cost row (a done card's completion stamp left, the cost flush right), then
 * the step row (step left, id flush right) — each inside the card, at 800px and at 390px/320px (`bzh:narrow-viewport-tier-rule`); and the card's own
 * controls sit over the open control stretched beneath it, so a real pointer reaches them. Under a real
 * browser — see `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep.
 */
const CARD: BoardCard = {
  chunkId: 'ch_01rowscard00000000000000000',
  shortId: 'C-ROWS',
  status: 'waiting_on_human',
  terminal: false,
  promotable: false,
  node: 'verify',
  nodeId: 'nd_verify',
  workLines: [{ label: 'hub#7', title: 'Migrate the event log onto the new store' }],
  itemsRead: true,
  costUsd: 1.23,
  costPartial: false,
  estimatedCostUsd: null,
  completedAt: null,
  blockedOn: 'ch_01prereq00000000000000000',
  blockedCount: 1,
  blockedOnStatus: 'running',
  openPrs: [{ repo: 'owner/repo', number: 7, url: 'https://example.test/pr/7' }],
};

// 800 (wider than any real board column) and 390/320 (the phone pair
// `app-panel-mobile.shell-sweep.spec.ts` already sweeps) — not a board column's own
// narrower fractional share of either.
const WIDTHS = [800, 390, 320];

async function renderCard(width: number, card: BoardCard = CARD, canControl = false): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardCardComponent],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardCardComponent);
  fixture.componentRef.setInput('card', card);
  fixture.componentRef.setInput('canControl', canControl);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return root;
}

function rectOf(root: HTMLElement, testid: string, width: number): DOMRect {
  const el = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
  expect(el, `width ${width}: fixture defect — ${testid} did not render`).not.toBeNull();
  return el!.getBoundingClientRect();
}

describe('board card row order shell sweep (web:shell-sweep)', () => {
  for (const width of WIDTHS) {
    it(`stacks title, blocked marking, PR links, cost row, and step row inside the card at width ${width}`, async () => {
      const root = await renderCard(width);
      try {
        const card = rectOf(root, 'chunk-card', width);
        const order = ['chunk-title', 'chunk-blocked-by', 'card-delivery', 'card-cost-row', 'chunk-node'];
        const rects = order.map((id) => rectOf(root, id, width));

        for (let i = 1; i < rects.length; i++) {
          expect(
            rects[i].top,
            `width ${width}: ${order[i]} (top ${rects[i].top}) does not sit below ${order[i - 1]} (bottom ${rects[i - 1].bottom})`,
          ).toBeGreaterThanOrEqual(rects[i - 1].bottom - 0.5);
        }
        for (let i = 0; i < rects.length; i++) {
          expect(rects[i].right, `width ${width}: ${order[i]} overflows the card`).toBeLessThanOrEqual(card.right + 0.5);
        }

        // The step row: the step leads at the card's content edge, the id ends on its line.
        const node = rects[order.indexOf('chunk-node')];
        const id = rectOf(root, 'chunk-id', width);
        const title = rects[0];
        expect(node.left, `width ${width}: the step is not left-aligned with the title`).toBeCloseTo(title.left, 0);
        expect(id.top, `width ${width}: the id left the step row`).toBeLessThan(node.bottom);
        expect(id.left, `width ${width}: the id overlaps the step`).toBeGreaterThanOrEqual(node.right - 0.5);
        const cost = rectOf(root, 'card-cost', width);
        expect(Math.abs(id.right - cost.right), `width ${width}: the id and the cost are not both flush right`).toBeLessThanOrEqual(0.5);
      } finally {
        root.remove();
      }
    });
  }

  for (const width of [800, 390]) {
    it(`tints the card with the project's color in place of a swatch, the step still leading its row, at width ${width}`, async () => {
      const root = await renderCard(width, { ...CARD, projectName: 'Blizzard', projectColor: 'teal' });
      try {
        const cardEl = root.querySelector<HTMLElement>('[data-testid="chunk-card"]')!;
        expect(cardEl.classList.contains('tinted'), `width ${width}: the card is not tinted`).toBe(true);
        expect(cardEl.style.getPropertyValue('--project-tint'), `width ${width}: the tint carries no color`).not.toBe('');
        expect(cardEl.getAttribute('title'), `width ${width}: the tinted card is not named for the project`).toBe('Blizzard');
        expect(root.querySelector('.swatch'), `width ${width}: a swatch still renders beside the tint`).toBeNull();

        const node = rectOf(root, 'chunk-node', width);
        const id = rectOf(root, 'chunk-id', width);
        const title = rectOf(root, 'chunk-title', width);
        const card = rectOf(root, 'chunk-card', width);
        expect(node.left, `width ${width}: the step does not lead its row`).toBeCloseTo(title.left, 0);
        expect(id.left, `width ${width}: the id overlaps the step`).toBeGreaterThanOrEqual(node.right - 0.5);
        expect(id.right, `width ${width}: the id left the card's right edge`).toBeLessThanOrEqual(card.right + 0.5);
      } finally {
        root.remove();
      }
    });
  }

  it('keeps a multi-prerequisite count in view, unclipped, at 320px', async () => {
    const root = await renderCard(320, { ...CARD, blockedCount: 4 });
    try {
      const marking = root.querySelector<HTMLElement>('[data-testid="chunk-blocked-by"]')!;
      const count = marking.querySelector<HTMLElement>('.blk-ref')!;

      expect(count.textContent).toBe('4 chunks');
      expect(count.getBoundingClientRect().right, 'the count is clipped off the marking').toBeLessThanOrEqual(
        marking.getBoundingClientRect().right + 0.5,
      );
    } finally {
      root.remove();
    }
  });

  for (const width of WIDTHS) {
    it(`lets a real pointer reach the PR link, the fold, and Promote over the open control at width ${width}`, async () => {
      const root = await renderCard(width, { ...CARD, workLines: ['One', 'Two', 'Three', 'Four'].map((title) => ({ label: null, title })), promotable: true }, true);
      try {
        for (const testid of ['card-pr-link', 'chunk-title-fold', 'promote-chunk']) {
          const control = root.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
          expect(control, `width ${width}: fixture defect — ${testid} did not render`).not.toBeNull();
          const rect = control!.getBoundingClientRect();
          const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          expect(hit && control!.contains(hit), `width ${width}: a pointer at ${testid} lands on ${hit?.outerHTML.slice(0, 80)}`).toBe(true);
        }
      } finally {
        root.remove();
      }
    });
  }
});
