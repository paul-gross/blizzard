import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { GraphSummaryView } from '../api/hub';
import { GraphExplorerList } from './graph-explorer-list';

/**
 * The graph explorer's two row levels, rebuilt on `KitSelectRow` — a real layout claim
 * jsdom cannot make. Both levels now render their content projected into another
 * component's button, so the row's own `.ge-group-line`/`.ge-row-line` flex rows are laid
 * out inside a box this file does not own; whether a long graph name, its version count,
 * and its right-anchored short id still fit one line without pushing past the list's own
 * edge is a question only a real layout answers.
 *
 * Swept at 320px alongside the wider cases (`bzh:narrow-viewport-tier-rule`), because the
 * explorer is a master column that gets narrow before anything else on the page does.
 *
 * Excluded from the default `ng test fleet` run (`angular.json`'s `test.exclude`) because
 * it needs `--browsers=ChromiumHeadless`, not jsdom — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */

/** One lineage whose name is long enough to compete with the row's trailing short id —
 * the case a row that merely renders short content would never exercise. */
const GRAPHS = [
  {
    graph_id: 'gr_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    name: 'a-deliberately-long-graph-name-that-wants-the-whole-row',
    version: 3,
    lifecycle: 'active',
    created_at: '2026-08-09T00:00:00.000Z',
  },
  {
    graph_id: 'gr_01KXKVVF1J3D6H6VYZ3XYN3YJ8',
    name: 'a-deliberately-long-graph-name-that-wants-the-whole-row',
    version: 2,
    lifecycle: 'retired',
    created_at: '2026-08-08T00:00:00.000Z',
  },
  {
    graph_id: 'gr_01KXKVVF1J3D6H6VYZ3XYN3YJ7',
    name: 'default',
    version: 1,
    lifecycle: 'active',
    created_at: '2026-08-07T00:00:00.000Z',
  },
] as unknown as GraphSummaryView[];

const WIDTHS = [520, 390, 320];

async function renderList(width: number): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [GraphExplorerList],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(GraphExplorerList);
  fixture.componentRef.setInput('graphs', GRAPHS);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  root.style.cssText = 'display: block; width: 100%;';
  document.body.appendChild(root);
  await page.viewport(width, 700);

  // Expand the first lineage, so the nested row level is genuinely laid out rather than
  // left collapsed — the level this rebuild changed most.
  root.querySelector<HTMLElement>('[data-testid="graph-explorer-group-toggle"]')!.click();
  fixture.detectChanges();
  await fixture.whenStable();
  await new Promise((resolve) => requestAnimationFrame(resolve));
  return root;
}

describe('graph explorer list row shell sweep (web:shell-sweep)', () => {
  for (const width of WIDTHS) {
    it(`keeps both row levels within the list's own edge at width ${width}`, async () => {
      const root = await renderList(width);
      try {
        const groups = root.querySelector<HTMLElement>('[data-testid="graph-explorer-groups"]');
        expect(groups, 'no graph-explorer-groups in the DOM').not.toBeNull();
        const listRect = groups!.getBoundingClientRect();

        const lineage = root.querySelector('[data-testid="graph-explorer-lineage"]');
        expect(lineage, `width ${width}: fixture defect — the lineage never expanded`).not.toBeNull();

        const cells = root.querySelectorAll<HTMLElement>(
          '[data-testid="graph-explorer-group-count"], [data-testid="graph-explorer-group-effective"], ' +
            '[data-testid="graph-explorer-graph-id"], [data-testid="graph-explorer-badge"], ' +
            '[data-testid="graph-explorer-show-retired"]',
        );
        expect(cells.length, `width ${width}: fixture defect — the row cells did not render`).toBeGreaterThan(4);

        for (const cell of Array.from(cells)) {
          const rect = cell.getBoundingClientRect();
          expect(
            rect.right,
            `width ${width}: ${cell.dataset['testid']}'s right edge (${rect.right}) overflows the list's own (${listRect.right})`,
          ).toBeLessThanOrEqual(listRect.right + 0.5);
        }
      } finally {
        root.remove();
      }
    });
  }
});
