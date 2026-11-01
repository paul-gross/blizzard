import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import type { BoardCard } from '../board-card/board-card-view.model';
import { LANES, type Lane, laneTone, toneColor } from 'fleet';
import { BoardColumn } from './board-column';

/** The board column's layout under a real browser — see `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep. */
const CARD: BoardCard = {
  chunkId: 'ch_01grip0000000000000000000',
  shortId: 'C-GRIP',
  status: 'ready',
  terminal: false,
  promotable: false,
  node: 'build',
  nodeId: 'nd_build',
  workLines: [],
  itemsRead: true,
  costUsd: 0,
  costPartial: false,
  estimatedCostUsd: null,
  completedAt: null,
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
};

const READY: Lane = { key: 'ready', label: 'READY', headerLabel: 'Ready' };
const BACKLOG: Lane = { key: 'notready', label: 'BACKLOG', headerLabel: 'Backlog' };
const RUNNING: Lane = { key: 'running', label: 'RUNNING', headerLabel: 'Running' };

async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
}

async function render(column: Lane, reorderControls: boolean, canReorder: boolean, count: number | null = null): Promise<HTMLElement> {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [BoardColumn],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(BoardColumn);
  fixture.componentRef.setInput('column', column);
  fixture.componentRef.setInput('cards', [CARD]);
  fixture.componentRef.setInput('reorderControls', reorderControls);
  fixture.componentRef.setInput('canReorder', canReorder);
  fixture.componentRef.setInput('count', count);
  await fixture.whenStable();
  const root = fixture.nativeElement as HTMLElement;
  root.style.width = '240px';
  document.body.appendChild(root);
  await page.viewport(320, 400);
  await new Promise((resolve) => requestAnimationFrame(resolve));
  return root;
}

describe('board-column reorder grip shell sweep (web:shell-sweep)', () => {
  it('renders a token-coloured left-side grip inside ready and backlog whole-card drags', async () => {
    await loadDesignTokens();
    for (const column of [READY, BACKLOG]) {
      const root = await render(column, true, true);
      try {
        const grip = root.querySelector<HTMLElement>('[data-testid="board-reorder-grip"]');
        expect(grip, `${column.label}: fixture defect — grip did not render`).not.toBeNull();
        expect(grip!.getAttribute('aria-hidden')).toBe('true');
        expect(root.querySelector('[cdkdraghandle]')).toBeNull();
        expect(root.querySelector('.q-card[cdkdrag]')).not.toBeNull();

        const dots = [...grip!.querySelectorAll<HTMLElement>('span')];
        expect(dots).toHaveLength(12);
        expect(getComputedStyle(dots[0]).backgroundColor).toBe('rgb(92, 209, 229)');
        const dotRects = dots.map((dot) => dot.getBoundingClientRect());
        expect(new Set(dotRects.map((rect) => Math.round(rect.left))).size, 'grip did not render exactly two columns').toBe(
          2,
        );
        expect(new Set(dotRects.map((rect) => Math.round(rect.top))).size, 'grip did not render exactly six rows').toBe(6);

        const card = root.querySelector<HTMLElement>('[data-testid="chunk-card"]');
        expect(card, `${column.label}: fixture defect — card did not render`).not.toBeNull();
        const gripRect = grip!.getBoundingClientRect();
        const cardRect = card!.getBoundingClientRect();
        expect(gripRect.left).toBeGreaterThanOrEqual(cardRect.left);
        expect(gripRect.right).toBeLessThanOrEqual(cardRect.right);
        expect(gripRect.top).toBeGreaterThanOrEqual(cardRect.top);
        expect(gripRect.bottom).toBeLessThanOrEqual(cardRect.bottom);
        expect(gripRect.right).toBeLessThanOrEqual(cardRect.left + cardRect.width / 2);
      } finally {
        root.remove();
      }
    }
  });

  it('does not render or arm a grip outside a permitted ranked lane', async () => {
    for (const [column, reorderControls, canReorder] of [
      [READY, true, false],
      [RUNNING, false, true],
    ] as const) {
      const root = await render(column, reorderControls, canReorder);
      try {
        expect(root.querySelector('[data-testid="board-reorder-grip"]')).toBeNull();
        expect(root.querySelector('[cdkdrag]')).toBeNull();
      } finally {
        root.remove();
      }
    }
  });
});

/** The computed color a bare token expression resolves to under the loaded design tokens. */
function resolvedColor(value: string): string {
  const probe = document.createElement('span');
  probe.style.color = value;
  document.body.appendChild(probe);
  try {
    return getComputedStyle(probe).color;
  } finally {
    probe.remove();
  }
}

describe('board-column count tone shell sweep (web:shell-sweep)', () => {
  it("draws each lane's count, and the beacon of each live lane, in its lane tone's token", async () => {
    await loadDesignTokens();
    for (const column of LANES) {
      const root = await render(column, false, false, 7);
      try {
        const count = root.querySelector<HTMLElement>('[data-testid="lane-count"]');
        expect(count, `${column.label}: fixture defect — count did not render`).not.toBeNull();
        const expected = resolvedColor(toneColor(laneTone(column.key)));
        expect(getComputedStyle(count!).color, `${column.label}: count color`).toBe(expected);

        const beacon = root.querySelector<HTMLElement>('[data-testid="lane-blink"] .beacon');
        if (['running', 'waiting', 'needs'].includes(column.key)) {
          expect(beacon, `${column.label}: beacon did not render`).not.toBeNull();
          expect(beacon!.getAttribute('style'), `${column.label}: beacon color`).toContain(toneColor(laneTone(column.key)));
        } else {
          expect(beacon, `${column.label}: a quiet lane beacons`).toBeNull();
        }
      } finally {
        root.remove();
      }
    }
  });
});
