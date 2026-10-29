import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { page } from 'vitest/browser';

import type { FlowPlaceView, FlowView, WorkItemsView } from 'fleet';
import { DeliveryBandView } from './delivery-band-view';
import { deliveryLanes } from './delivery-band.model';

/**
 * The Delivery band's lanes at their fullest — a lane landing one chunk with three in line (one polling, one in a state
 * this build does not know), and a collapsed lane of its last verdict alone — stay inside the band at 1440px and at the
 * narrowest centre column the desktop board renders, as `board-list.shell-sweep.spec.ts` frames it: a 768px viewport
 * less the 260px rail and the page's gutters. A lane wraps rather than overflowing, a collapsed lane folds to one line, and the unknown state reads raw. Under a real
 * browser — see `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep.
 */
const NOW = Date.parse('2026-10-29T12:00:00Z');

const place = (chunk: string, arrival: number, state: string, nextPollAt: string | null = null): FlowPlaceView => ({
  chunk_id: chunk,
  submission_id: `sub_${arrival}`,
  arrival,
  state: state as FlowPlaceView['state'],
  position: arrival,
  next_poll_at: nextPollAt,
});

const FLOWS: FlowView[] = [
  {
    flow_id: 'flw_busy',
    resource: 'repo_1:master',
    repository_id: 'repo_1',
    repository_name: 'blizzard',
    repository_slug: 'paul-gross/blizzard',
    branch: 'master',
    turn: { chunk_id: 'ch_01landing000000000000000000', started_at: '2026-10-29T11:58:00Z' },
    line: [
      place('ch_01landing000000000000000000', 1, 'landing'),
      place('ch_01next00000000000000000000', 2, 'next'),
      place('ch_01polling00000000000000000', 3, 'polling', '2026-10-29T12:05:00Z'),
      place('ch_01unknown00000000000000000', 4, 'awaiting-merge'),
    ],
    last_verdict: {
      id: 7, flow_id: 'flw_busy', chunk_id: 'ch_01done0000000000000000000', submission_id: 'sub_0', kind: 'turn-ended',
      outcome: 'landed', revision: null, at: '2026-10-29T11:40:00Z',
    },
    last_landing: null,
  },
  {
    flow_id: 'flw_quiet',
    resource: 'repo_2:release/2026-10',
    repository_id: 'repo_2',
    repository_name: 'blizzard-context',
    repository_slug: 'paul-gross/blizzard-context',
    branch: 'release/2026-10',
    turn: null,
    line: [],
    last_verdict: {
      id: 3, flow_id: 'flw_quiet', chunk_id: 'ch_01old00000000000000000000', submission_id: 'sub_9', kind: 'turn-ended',
      outcome: 'landed', revision: null, at: '2026-10-29T09:00:00Z',
    },
    last_landing: null,
  },
];

const TITLES = new Map<string, WorkItemsView>(
  ['ch_01landing000000000000000000', 'ch_01next00000000000000000000', 'ch_01polling00000000000000000', 'ch_01unknown00000000000000000'].map(
    (chunk) => [chunk, { items: [{ title: 'Migrate the event log onto the new store and retire the old one' } as WorkItemsView['items'][number]] }],
  ),
);

/** The band's own width: a full 1440px desktop, and the narrowest centre column it gets. */
const WIDTHS = [1440, 490];

async function renderBand(width: number): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [DeliveryBandView],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(DeliveryBandView);
  fixture.componentRef.setInput('lanes', deliveryLanes(FLOWS, TITLES, NOW));
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  root.style.width = `${width}px`;
  document.body.appendChild(root);
  await page.viewport(1440, 600);
  await new Promise((resolve) => requestAnimationFrame(resolve));
  return root;
}

function inside(inner: DOMRect, outer: DOMRect): boolean {
  return inner.left >= outer.left - 0.5 && inner.right <= outer.right + 0.5;
}

describe('delivery band shell sweep (web:shell-sweep)', () => {
  for (const width of WIDTHS) {
    it(`keeps every lane's chunks and verdict inside the band at width ${width}`, async () => {
      const root = await renderBand(width);
      try {
        const band = root.querySelector<HTMLElement>('[data-testid="delivery-band"]')!;
        expect(band, 'fixture defect — the band did not render').not.toBeNull();
        expect(band.scrollWidth, `width ${width}: the band overflows`).toBeLessThanOrEqual(band.clientWidth);
        const bandRect = band.getBoundingClientRect();
        const parts = band.querySelectorAll<HTMLElement>(
          '[data-testid="delivery-lane-title"], [data-testid="delivery-landing"], [data-testid="delivery-in-line"], [data-testid="delivery-verdict"]',
        );
        expect(parts.length).toBe(8);
        for (const part of parts) {
          expect(inside(part.getBoundingClientRect(), bandRect), `width ${width}: ${part.dataset['testid']} leaves the band`).toBe(true);
        }
      } finally {
        root.remove();
      }
    });

    it(`folds a collapsed lane to one line at width ${width}`, async () => {
      const root = await renderBand(width);
      try {
        const lane = root.querySelector<HTMLElement>('[data-flow="flw_quiet"]')!;
        expect(lane.classList).toContain('collapsed');
        const title = lane.querySelector<HTMLElement>('[data-testid="delivery-lane-title"]')!.getBoundingClientRect();
        const verdict = lane.querySelector<HTMLElement>('[data-testid="delivery-verdict"]')!.getBoundingClientRect();
        expect(Math.abs(title.top - verdict.top), `width ${width}: the verdict wraps below the title`).toBeLessThan(4);
        expect(lane.getBoundingClientRect().height).toBeLessThan(title.height * 1.8);
      } finally {
        root.remove();
      }
    });

    it(`reads an unknown state raw, visible inside its chunk, at width ${width}`, async () => {
      const root = await renderBand(width);
      try {
        const chunk = root.querySelector<HTMLElement>('[data-chunk="ch_01unknown00000000000000000"]')!;
        const badge = chunk.querySelector<HTMLElement>('[data-testid="delivery-state"]')!;
        expect(badge.textContent?.trim()).toBe('awaiting-merge');
        const rect = badge.getBoundingClientRect();
        expect(rect.width).toBeGreaterThan(0);
        expect(inside(rect, chunk.getBoundingClientRect()), `width ${width}: the state leaves its chunk`).toBe(true);
      } finally {
        root.remove();
      }
    });
  }
});
