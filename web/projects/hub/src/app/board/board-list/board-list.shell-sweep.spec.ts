import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import type { BoardCard } from '../board-card/board-card';
import { BoardList, type BoardListEntry, type BoardListGroups } from './board-list';

/**
 * The board list's layout under a real browser: every row — lane icon, work, step, runner, cost —
 * stays inside the list's own width, an armed row's grip clears its lane icon, and the
 * READY/BACKLOG line sits between the two ranked runs. Swept at a wide centre column and at the
 * narrowest one the desktop board renders: a 768px viewport (below it the hub serves the mobile
 * glance board instead) less the 260px rail and the page's gutters. See
 * `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep.
 */
const CARD: BoardCard = {
  chunkId: 'ch_01list0000000000000000000',
  shortId: 'C-LIST',
  status: 'running',
  terminal: false,
  promotable: false,
  node: 'build',
  nodeId: 'nd_build',
  workLines: [{ label: 'hub#7', title: 'Migrate the event log onto the new store, partitioned by tenant', url: 'https://example.test/7' }],
  itemsRead: true,
  costUsd: 1.23,
  costPartial: false,
  estimatedCostUsd: null,
  completedAt: null,
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
  runnerId: 'rn_01M49GZR9G2S7PT1TB5ZT4E4AX',
  runnerName: 'runner-with-a-long-display-name',
  openPrs: [{ repo: 'owner/repo', number: 7, url: 'https://example.test/pr/7' }],
};

const entry = (lane: string, suffix: string, fields: Partial<BoardCard> = {}): BoardListEntry => ({
  lane,
  card: { ...CARD, chunkId: `ch_01list${suffix}`, shortId: `C-${suffix}`, ...fields },
});

const GROUPS: BoardListGroups = {
  live: [entry('running', 'RUN1')],
  ready: [entry('ready', 'RDY1', { status: 'ready' }), entry('ready', 'RDY2', { status: 'ready' })],
  backlog: [entry('notready', 'BKL1', { status: 'not_ready' })],
  done: [entry('done', 'DNE1', { status: 'done', terminal: true, completedAt: '2026-07-20T09:30:00Z' })],
};

/** The list's own width: a wide centre column, and the narrowest the desktop board gives it. */
const WIDTHS = [1100, 490];

async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
}

async function render(width: number): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardList],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardList);
  fixture.componentRef.setInput('groups', GROUPS);
  fixture.componentRef.setInput('loaded', true);
  fixture.componentRef.setInput('canReorder', true);
  fixture.componentRef.setInput('canControl', true);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  root.style.width = `${width}px`;
  document.body.appendChild(root);
  await page.viewport(1280, 900);
  await new Promise((resolve) => requestAnimationFrame(resolve));
  return root;
}

describe('board list shell sweep (web:shell-sweep)', () => {
  beforeAll(loadDesignTokens);

  for (const width of WIDTHS) {
    it(`keeps every row inside the list, the grip clear of the lane icon, and the line between READY and BACKLOG at width ${width}`, async () => {
      const root = await render(width);
      try {
        const list = root.querySelector<HTMLElement>('[data-testid="board-list"]')!.getBoundingClientRect();
        const rows = [...root.querySelectorAll<HTMLElement>('[data-testid="list-row"]')];
        expect(rows, 'fixture defect — wrong row count').toHaveLength(5);
        for (const row of rows) {
          const id = row.getAttribute('data-chunk');
          const rect = row.getBoundingClientRect();
          expect(rect.right, `width ${width}: row ${id} overflows the list`).toBeLessThanOrEqual(list.right + 0.5);
          for (const cell of row.querySelectorAll<HTMLElement>(':scope > :not(.row-open)')) {
            expect(cell.getBoundingClientRect().right, `width ${width}: a cell of row ${id} overflows its row`).toBeLessThanOrEqual(rect.right + 0.5);
          }
        }

        const grips = [...root.querySelectorAll<HTMLElement>('[data-testid="list-reorder-grip"]')];
        expect(grips, `width ${width}: the ranked rows did not arm`).toHaveLength(3);
        for (const grip of grips) {
          const icon = grip.parentElement!.querySelector<HTMLElement>('[data-testid="list-row-lane"]')!.getBoundingClientRect();
          expect(grip.getBoundingClientRect().right, `width ${width}: the grip runs into the lane icon`).toBeLessThanOrEqual(icon.left + 0.5);
        }

        const divider = root.querySelector<HTMLElement>('[data-testid="list-ready-divider"]')!.getBoundingClientRect();
        const lastReady = root.querySelector<HTMLElement>('[data-chunk="ch_01listRDY2"]')!.getBoundingClientRect();
        const firstBacklog = root.querySelector<HTMLElement>('[data-chunk="ch_01listBKL1"]')!.getBoundingClientRect();
        expect(divider.top, `width ${width}: the line is not below READY`).toBeGreaterThanOrEqual(lastReady.bottom - 0.5);
        expect(divider.bottom, `width ${width}: the line is not above BACKLOG`).toBeLessThanOrEqual(firstBacklog.top + 0.5);
      } finally {
        root.remove();
      }
    });
  }
});
