import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import { type ChunkDetail, RUNNER_NAME_SEPARATOR, compactRef } from 'fleet';
import { ChunkDetailHeader } from './chunk-detail-header';

/**
 * The dock header's action row at narrow widths — a real
 * layout claim jsdom cannot make: it never actually lays out `.d-meta`/`.d-actions`'s
 * flex row, so `web:unit-test` cannot see a control pushed past the dock's own edge.
 * This mounts the header with every in-flow control live at once — a routed,
 * pausable chunk with a long runner identity — the worst case the row can carry
 * (Pause and the `⋯` overflow trigger, plus the close button; Detach, Complete, and
 * Delete live in the trigger's own menu panel), and sweeps that nothing overflows
 * the dock's own right edge, at 800px (wider than any real dock share) and at
 * 390/320px (`bzh:narrow-viewport-tier-rule`). A second case opens the menu and
 * sweeps its own panel items at the same widths — a real CDK overlay, not the
 * `.d-actions` flex row, so it needs its own layout claim. A third keeps the claim
 * line's compact runner id — what tells two runners sharing a name apart — inside the
 * chip's visible box however much of the name it clips.
 *
 * The selector list below is asserted against an exact count, not merely non-empty:
 * a hard-coded list that silently misses a newly added control is a sweep that stays
 * green over the very control it exists to measure.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const DETAIL: ChunkDetail = {
  chunk_id: 'ch_01dockwidth0000000000000000',
  graph_id: 'gr_1',
  status: 'ready',
  pausable: true,
  completable: true,
  deletable: true,
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  route: {
    runner_id: 'rn_01M49GZR9G2S7PT1TB5ZT4E4AX',
    runner_name: 'a-long-runner-name-that-wraps-under-a-narrow-column',
    workspace_id: 'ws_01',
    environment_ids: ['env_01'],
  },
};

/** The same chunk claimed by a runner with a typical name — 12 characters, inside the 13 the claim line holds whole. */
const TYPICAL_NAME_DETAIL: ChunkDetail = { ...DETAIL, route: { ...DETAIL.route!, runner_name: 'runner-local' } };

const WIDTHS = [800, 390, 320];

/** Every in-flow control the fixture above makes live at once — Detach, Complete,
 * and Delete live in the `⋯` trigger's own menu. */
const SWEPT = ['pause-chunk', 'chunk-actions-menu', 'detail-close'] as const;

/** The menu panel's own items, once opened — a routed, pausable, deletable chunk
 * (`blocking` gate open) makes all three live at once. */
const MENU_ITEMS = ['detach-chunk', 'complete-chunk', 'delete-chunk'] as const;

async function renderHeader(
  width: number,
  detail: ChunkDetail = DETAIL,
): Promise<{ root: HTMLElement; fixture: ReturnType<typeof TestBed.createComponent<ChunkDetailHeader>> }> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [ChunkDetailHeader],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkDetailHeader);
  fixture.componentRef.setInput('detail', detail);
  fixture.componentRef.setInput('renderedStatus', detail.status);
  fixture.componentRef.setInput('canControl', true);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return { root, fixture };
}

describe('chunk detail header action row shell sweep (web:shell-sweep)', () => {
  for (const width of WIDTHS) {
    it(`keeps every dock control within the header's own edge at width ${width}`, async () => {
      const { root } = await renderHeader(width);
      try {
        // The host is `display: contents` (no box of its own) — `.d-head` is the
        // actual header element every control's edge is measured against.
        const headerRect = root.querySelector('.d-head')!.getBoundingClientRect();
        const controls = root.querySelectorAll<HTMLElement>(SWEPT.map((t) => `[data-testid="${t}"]`).join(', '));
        expect(
          controls.length,
          `width ${width}: fixture defect — expected every control in SWEPT to render, got ` +
            Array.from(controls)
              .map((c) => c.dataset['testid'])
              .join(', '),
        ).toBe(SWEPT.length);
        for (const control of Array.from(controls)) {
          const rect = control.getBoundingClientRect();
          expect(
            rect.right,
            `width ${width}: ${control.dataset['testid']}'s right edge (${rect.right}) overflows the header's own (${headerRect.right})`,
          ).toBeLessThanOrEqual(headerRect.right + 0.5);
        }
      } finally {
        root.remove();
      }
    });

    it(`keeps the opened ⋯ menu's own items on-viewport at width ${width}`, async () => {
      const { root, fixture } = await renderHeader(width);
      try {
        root.querySelector<HTMLElement>('[data-testid="chunk-actions-menu"]')?.click();
        await fixture.whenStable();

        // The CDK renders the panel into an overlay attached to `document.body`,
        // not inside the fixture's own element (`kit-menu.spec.ts`'s own convention).
        const items = document.body.querySelectorAll<HTMLElement>(MENU_ITEMS.map((t) => `[data-testid="${t}"]`).join(', '));
        expect(
          items.length,
          `width ${width}: fixture defect — expected every item in MENU_ITEMS to render, got ` +
            Array.from(items)
              .map((c) => c.dataset['testid'])
              .join(', '),
        ).toBe(MENU_ITEMS.length);
        for (const item of Array.from(items)) {
          const rect = item.getBoundingClientRect();
          expect(
            rect.right,
            `width ${width}: menu item ${item.dataset['testid']}'s right edge (${rect.right}) overflows the viewport (${width})`,
          ).toBeLessThanOrEqual(width + 0.5);
          expect(rect.left, `width ${width}: menu item ${item.dataset['testid']} renders off-screen to the left`).toBeGreaterThanOrEqual(-0.5);
        }
      } finally {
        root.remove();
      }
    });

    it(`keeps the claim line's compact runner id readable at width ${width}`, async () => {
      const { root } = await renderHeader(width);
      try {
        const chip = root.querySelector<HTMLElement>('[data-testid="route-info"]')!;
        const text = chip.firstChild as Text;
        // Through the separator: an ellipsis that clips the name lands on it at the earliest.
        const lead = `Claimed by ${compactRef(DETAIL.route!.runner_id)}${RUNNER_NAME_SEPARATOR}`;
        expect(text.data.startsWith(lead), `width ${width}: the chip reads ${JSON.stringify(text.data)}`).toBe(true);
        const range = document.createRange();
        range.setStart(text, 0);
        range.setEnd(text, lead.length);
        const visibleRight = chip.getBoundingClientRect().left + chip.clientWidth;
        expect(
          range.getBoundingClientRect().right,
          `width ${width}: the chip clips its lead ${JSON.stringify(lead)} (visible to ${visibleRight})`,
        ).toBeLessThanOrEqual(visibleRight + 0.5);
      } finally {
        root.remove();
      }
    });
  }

  it('shows a typical display name whole on the claim line at width 800', async () => {
    const { root } = await renderHeader(800, TYPICAL_NAME_DETAIL);
    try {
      const chip = root.querySelector<HTMLElement>('[data-testid="route-info"]')!;
      expect(chip.textContent).toBe('Claimed by R-E4AX.runner-local');
      expect(chip.scrollWidth, `the chip clips ${JSON.stringify(chip.textContent)}`).toBeLessThanOrEqual(chip.clientWidth);
    } finally {
      root.remove();
    }
  });
});
