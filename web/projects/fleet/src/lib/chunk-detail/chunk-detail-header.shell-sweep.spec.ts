import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { page } from 'vitest/browser';

import type { ChunkDetail } from '../api/hub';
import { ChunkDetailHeader } from './chunk-detail-header';

/**
 * The dock header's action row at narrow widths (issue #461 round 3 F4) — a real
 * layout claim jsdom cannot make: it never actually lays out `.d-meta`/`.d-actions`'s
 * flex row, so `web:unit-test` cannot see a control pushed past the dock's own edge.
 * This mounts the header with every control live at once — a routed, pausable,
 * blocked chunk with a long runner identity — the worst case the row can carry
 * (Pause, Complete, the prerequisite field, Declare, Release, plus the route/Detach
 * group and the close button), and sweeps that nothing overflows the dock's own
 * right edge, at 800px (wider than any real dock share) and at 390/320px
 * (`bzh:narrow-viewport-tier-rule`).
 *
 * Excluded from the default `ng test` run the same way every other
 * `*.shell-sweep.spec.ts` is — run it via `npm run shell-sweep`
 * (`web/scripts/shell-sweep.js`).
 */
const DETAIL: ChunkDetail = {
  chunk_id: 'ch_01dockwidth0000000000000000',
  graph_id: 'gr_1',
  status: 'ready',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  blocked: { prerequisite_chunk_id: 'ch_01prereq00000000000000000' },
  route: { runner_id: 'a-long-runner-identity-that-wraps-under-a-narrow-column', workspace_id: 'ws_01', environment_ids: ['env_01'] },
};

const WIDTHS = [800, 390, 320];

async function renderHeader(width: number): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [ChunkDetailHeader],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkDetailHeader);
  fixture.componentRef.setInput('detail', DETAIL);
  fixture.componentRef.setInput('canControl', true);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  document.body.appendChild(root);
  await page.viewport(width, 400);
  return root;
}

describe('chunk detail header action row shell sweep (web:shell-sweep, issue #461)', () => {
  for (const width of WIDTHS) {
    it(`keeps every dock control within the header's own edge at width ${width}`, async () => {
      const root = await renderHeader(width);
      try {
        // The host is `display: contents` (no box of its own) — `.d-head` is the
        // actual header element every control's edge is measured against.
        const headerRect = root.querySelector('.d-head')!.getBoundingClientRect();
        const controls = root.querySelectorAll<HTMLElement>(
          '[data-testid="pause-chunk"], [data-testid="complete-chunk"], ' +
            '[data-testid="dependency-prerequisite-input"], [data-testid="declare-dependency"], ' +
            '[data-testid="release-dependency"], [data-testid="detach-chunk"], [data-testid="detail-close"]',
        );
        expect(controls.length, `width ${width}: fixture defect — not every control rendered`).toBeGreaterThan(0);
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
  }
});
