import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { commands, page } from 'vitest/browser';

import type { ChunkDetail } from '../api/hub';
import { ChunkNeighborhood } from './chunk-neighborhood';

/** The design tokens are a global stylesheet loaded via each app's build `styles`, never
 * by a standalone component test (`hover-tint.shell-sweep.spec.ts`'s own `loadDesignTokens`)
 * — this spec's satisfied-vs-unmet claim is about resolved `var(--green)`/`var(--amber-hi)`
 * color, so it reads the sheet's real text server-side and injects it as a `<style>`
 * element itself, the same way. */
async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/design/tokens.css');
  const styleEl = document.createElement('style');
  styleEl.textContent = css;
  document.head.appendChild(styleEl);
}

/**
 * The chunk neighborhood's own two claims real layout must make (issue #462):
 *
 * - A satisfied edge's own {@link fleet-kit-badge} renders a genuinely different computed
 *   color from an unmet one — jsdom parses `[tone]="satisfiedTone(n)"` without resolving
 *   it against `kit-badge.ts`'s `TONE_COLOR` ladder, so `web:unit-test` cannot see the two
 *   satisfied-vs-unmet rows actually differ on screen (`bzh:visual-change-needs-a-render`).
 * - Several neighbors in each direction wrap onto their own lines without overflowing the
 *   panel at 390px/320px (`bzh:narrow-viewport-tier-rule`) — the surface this mounts on
 *   (the dock and the routed chunk page) is reachable from the mobile board.
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const DETAIL: ChunkDetail = {
  chunk_id: 'ch_01subject00000000000000000',
  graph_id: 'gr_1',
  status: 'ready',
  current_node_id: null,
  latest_epoch: null,
  work_refs: [],
  history: [],
  artifacts: [],
  neighborhood: {
    prerequisites: [
      { chunk_id: 'ch_01satisfied0000000000000a', status: 'done', satisfied: true },
      { chunk_id: 'ch_01unmet00000000000000000b', status: 'not_ready', satisfied: false },
      { chunk_id: 'ch_01unmet00000000000000000c', status: 'running', satisfied: false },
      { chunk_id: 'ch_01unmet00000000000000000d', status: 'waiting_on_human', satisfied: false },
    ],
    dependents: [
      { chunk_id: 'ch_01dependent000000000000e', status: 'ready', satisfied: false },
      { chunk_id: 'ch_01dependent000000000000f', status: 'not_ready', satisfied: false },
    ],
  },
};

async function render(width: number): Promise<HTMLElement> {
  await loadDesignTokens();
  await TestBed.configureTestingModule({
    imports: [ChunkNeighborhood],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkNeighborhood);
  fixture.componentRef.setInput('detail', DETAIL);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 500);
  await new Promise((resolve) => requestAnimationFrame(resolve));
  return root;
}

describe('chunk neighborhood shell sweep (web:shell-sweep, issue #462)', () => {
  it('renders a satisfied edge with a genuinely different computed color from an unmet one', async () => {
    const root = await render(800);
    try {
      const badges = Array.from(root.querySelectorAll<HTMLElement>('[data-testid="neighbor-satisfied"]'));
      expect(badges.length, 'fixture defect: no satisfied badges rendered').toBeGreaterThan(0);

      // `KitBadge` sets `[style.color]` on its own internal `.badge` span, not on the
      // `fleet-kit-badge` host — reading the host's own computed color would just see the
      // inherited default and pass by accident.
      const badgeColor = (b: HTMLElement) => getComputedStyle(b.querySelector('.badge')!).color;
      const satisfiedColors = new Set(badges.filter((b) => b.textContent?.trim() === 'satisfied').map(badgeColor));
      const unmetColors = new Set(badges.filter((b) => b.textContent?.trim() === 'unmet').map(badgeColor));
      expect(satisfiedColors.size, 'fixture defect: no satisfied rows rendered').toBeGreaterThan(0);
      expect(unmetColors.size, 'fixture defect: no unmet rows rendered').toBeGreaterThan(0);

      for (const satisfiedColor of satisfiedColors) {
        for (const unmetColor of unmetColors) {
          expect(
            satisfiedColor,
            `satisfied color (${satisfiedColor}) does not differ from unmet color (${unmetColor})`,
          ).not.toBe(unmetColor);
        }
      }
    } finally {
      root.remove();
    }
  });

  for (const width of [390, 320]) {
    it(`wraps several neighbors in each direction without overflowing the panel at width ${width}`, async () => {
      const root = await render(width);
      try {
        const neighbors = root.querySelectorAll<HTMLElement>('[data-testid="neighbor"]');
        expect(neighbors.length, 'fixture defect: no neighbor rows rendered').toBeGreaterThan(0);

        const panel = root.querySelector<HTMLElement>('[data-testid="chunk-neighborhood"]')!;
        expect(
          panel.scrollWidth,
          `width ${width}: neighborhood overflows horizontally (${panel.scrollWidth} > ${panel.clientWidth})`,
        ).toBeLessThanOrEqual(panel.clientWidth);

        for (const neighbor of Array.from(neighbors)) {
          const rect = neighbor.getBoundingClientRect();
          const panelRect = panel.getBoundingClientRect();
          expect(
            rect.right,
            `width ${width}: a neighbor row's right edge (${rect.right}) overflows the panel's own (${panelRect.right})`,
          ).toBeLessThanOrEqual(panelRect.right + 0.5);
        }
      } finally {
        root.remove();
      }
    });
  }
});
