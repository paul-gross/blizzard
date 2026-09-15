import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import type { BoardCard } from '../board-card/board-card';
import type { Lane } from '../chunk-lanes';
import { BoardColumn } from './board-column';

const CARD: BoardCard = {
  chunkId: 'ch_01grip0000000000000000000',
  shortId: 'C-GRIP',
  status: 'ready',
  node: 'build',
  nodeId: 'nd_build',
  pointerLabels: [],
  costUsd: 0,
  costPartial: false,
  completedAt: null,
  blockedOn: null,
  blockedCount: 0,
  blockedOnStatus: null,
};

const READY: Lane = { key: 'ready', label: 'READY', headerLabel: 'Ready' };
const BACKLOG: Lane = { key: 'notready', label: 'BACKLOG', headerLabel: 'Backlog' };
const RUNNING: Lane = { key: 'running', label: 'RUNNING', headerLabel: 'Running' };

async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/design/tokens.css');
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
}

async function render(column: Lane, reorderControls: boolean, canReorder: boolean): Promise<HTMLElement> {
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
