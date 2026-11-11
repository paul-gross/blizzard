import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page, userEvent } from 'vitest/browser';

import type { ChunkStatus, ChunkSummary } from 'fleet';
import { chunkSummary } from 'fleet/testing';
import { BoardShell } from './board-shell';

/**
 * A long current-node name truncates inside its card rather than widening its lane: every
 * lane keeps its equal width inside a 1440px viewport, and the full name stays readable
 * from the card's `title`. A chunk whose status this build does not know opens a seventh lane
 * headed with the raw status, its card inside it, every lane still equal-width — at 1440px and
 * at the narrowest centre column the desktop board renders (`board-list.shell-sweep.spec.ts`'s
 * framing: a 768px viewport less the rail and the page's gutters). Under a real browser — see
 * `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep.
 */
const LONG_NODE = 'n'.repeat(120);

const RUNNING: ChunkSummary = chunkSummary({
  chunk_id: 'ch_01longnode000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_long',
  current_node_name: LONG_NODE,
  work_refs: [],
});

const NEWER_STATUS: ChunkSummary = chunkSummary({
  chunk_id: 'ch_01unknownstatus00000000000',
  graph_id: 'gr_1',
  status: 'newer-status' as ChunkStatus,
  current_node_id: 'nd_review',
  current_node_name: 'review',
});

/** The board's own width: a full 1440px desktop, and the narrowest centre column it gets. */
const UNKNOWN_LANE_WIDTHS = [1440, 490];

async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
}

describe('board shell long node name shell sweep (web:shell-sweep)', () => {
  for (const width of [320, 390]) {
    it(`keeps standalone board layouts scrollable at ${width}px`, async () => {
      await loadDesignTokens();
      TestBed.resetTestingModule();
      await TestBed.configureTestingModule({ imports: [BoardShell], providers: [provideZonelessChangeDetection()] }).compileComponents();
      const fixture = TestBed.createComponent(BoardShell);
      fixture.componentRef.setInput('chunks', [RUNNING]);
      fixture.componentRef.setInput('state', 'ready');
      const root = fixture.nativeElement as HTMLElement;
      root.style.cssText = `display:block;width:${width}px;height:600px`;
      document.body.appendChild(root);
      await page.viewport(width, 700);
      await fixture.whenStable();
      try {
        const board = root.querySelector<HTMLElement>('.board')!;
        expect(board.scrollWidth).toBeGreaterThan(board.clientWidth);
        const cue = root.querySelector<HTMLElement>('[data-testid="board-scroll-cue"]')!;
        expect(getComputedStyle(cue).display).not.toBe('none');
        expect(cue.getBoundingClientRect().right).toBeLessThanOrEqual(width);
        const tabs = root.querySelectorAll<HTMLElement>('fleet-kit-tabs [role="tab"]');
        expect(tabs).toHaveLength(3);
        for (const tab of tabs) {
          expect(tab.scrollWidth, `${tab.textContent} is clipped`).toBeLessThanOrEqual(tab.clientWidth + 1);
          expect(tab.getBoundingClientRect().right).toBeLessThanOrEqual(width);
        }
        board.focus();
        expect(document.activeElement).toBe(board);
        await userEvent.keyboard('{ArrowRight}');
        await expect.poll(() => board.scrollLeft).toBeGreaterThan(0);
        expect(root.querySelector<HTMLElement>('app-board-column')!.getBoundingClientRect().width).toBeGreaterThanOrEqual(200);
        fixture.componentRef.setInput('layout', 'list');
        await fixture.whenStable();
        const list = root.querySelector<HTMLElement>('app-board-list')!;
        expect(getComputedStyle(root.querySelector<HTMLElement>('[data-testid="board-scroll-cue"]')!).display).not.toBe('none');
        expect(list.scrollWidth).toBeGreaterThan(list.clientWidth);
        list.scrollLeft = list.scrollWidth;
        expect(list.scrollLeft).toBeGreaterThan(0);
      } finally {
        fixture.destroy();
        root.remove();
      }
    });
  }

  it('keeps a long done policy keyboard-scrollable inside a short board', async () => {
    await loadDesignTokens();
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({ imports: [BoardShell], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(BoardShell);
    const explanation = `${'Page cutoff 2026-11-01T10:00:00Z, '.repeat(30)}END OF POLICY`;
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('donePolicy', { heading: 'Done', explanation, cutoffs: [] });
    const root = fixture.nativeElement as HTMLElement;
    root.style.cssText = 'display:block;width:490px;height:180px';
    document.body.appendChild(root);
    await page.viewport(1024, 700);
    await fixture.whenStable();
    try {
      const summary = root.querySelector<HTMLElement>('[data-col="done"] summary')!;
      summary.focus();
      await userEvent.keyboard('{Enter}');
      await fixture.whenStable();
      const text = root.querySelector<HTMLElement>('.policy-text')!;
      expect(text.textContent).toBe(explanation);
      expect(text.scrollHeight).toBeGreaterThan(text.clientHeight);
      text.focus();
      expect(document.activeElement).toBe(text);
      await userEvent.keyboard('{End}');
      await expect.poll(() => text.scrollTop + text.clientHeight).toBeGreaterThanOrEqual(text.scrollHeight - 1);
      const panel = root.querySelector<HTMLElement>('.p-body')!.getBoundingClientRect();
      const bounds = text.getBoundingClientRect();
      expect(bounds.bottom).toBeLessThanOrEqual(panel.bottom + 1);
      expect(bounds.top).toBeGreaterThanOrEqual(panel.top - 1);
      const range = document.createRange();
      range.selectNodeContents(text);
      range.setStart(text.firstChild!, explanation.length - 'END OF POLICY'.length);
      const end = range.getBoundingClientRect();
      expect(end.bottom).toBeLessThanOrEqual(bounds.bottom + 1);
      expect(end.top).toBeGreaterThanOrEqual(bounds.top - 1);
    } finally {
      fixture.destroy();
      root.remove();
    }
  });

  it('keeps every lane equal-width and inside a 1440px viewport and the full name on hover', async () => {
    await loadDesignTokens();
    TestBed.resetTestingModule();
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', [RUNNING]);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    root.style.display = 'block';
    root.style.height = '600px';
    document.body.appendChild(root);
    await page.viewport(1440, 700);
    await new Promise((resolve) => requestAnimationFrame(resolve));
    try {
      const columns = [...root.querySelectorAll<HTMLElement>('app-board-column')];
      expect(getComputedStyle(root.querySelector<HTMLElement>('[data-testid="board-scroll-cue"]')!).display).toBe('none');
      expect(columns.length, 'fixture defect — lanes did not render').toBeGreaterThan(1);
      for (const column of columns) {
        const rect = column.getBoundingClientRect();
        expect(rect.right, `${column.dataset['col']} lane runs past the viewport`).toBeLessThanOrEqual(1440);
      }
      const widths = columns.map((column) => column.getBoundingClientRect().width);
      expect(Math.max(...widths) - Math.min(...widths), `lanes lost their equal widths: ${widths.join(', ')}`).toBeLessThan(2);
      const node = root.querySelector<HTMLElement>('[data-testid="chunk-node"]');
      expect(node!.getAttribute('title')).toContain(LONG_NODE);
      expect(node!.scrollWidth, 'node name is not clipped').toBeGreaterThan(node!.clientWidth);
    } finally {
      root.remove();
    }
  });

  for (const width of UNKNOWN_LANE_WIDTHS) {
    it(`heads a lane with an unknown raw status, its card inside it and every lane equal-width, at width ${width}`, async () => {
      await loadDesignTokens();
      TestBed.resetTestingModule();
      await TestBed.configureTestingModule({
        imports: [BoardShell],
        providers: [provideZonelessChangeDetection()],
      }).compileComponents();
      const fixture = TestBed.createComponent(BoardShell);
      fixture.componentRef.setInput('chunks', [RUNNING, NEWER_STATUS]);
      fixture.componentRef.setInput('state', 'ready');
      await fixture.whenStable();
      const root = fixture.nativeElement as HTMLElement;
      root.style.display = 'block';
      root.style.width = `${width}px`;
      root.style.height = '600px';
      document.body.appendChild(root);
      await page.viewport(1440, 700);
      await new Promise((resolve) => requestAnimationFrame(resolve));
      try {
        const bounds = root.getBoundingClientRect();
        const columns = [...root.querySelectorAll<HTMLElement>('app-board-column')];
        expect(columns, `width ${width}: the unknown lane did not open beside the five`).toHaveLength(6);
        for (const column of columns) {
          const rect = column.getBoundingClientRect();
          expect(rect.right, `width ${width}: ${column.dataset['col']} lane runs past the board`).toBeLessThanOrEqual(bounds.right + 0.5);
          expect(rect.right, `width ${width}: ${column.dataset['col']} lane runs past the viewport`).toBeLessThanOrEqual(1440);
        }
        const widths = columns.map((column) => column.getBoundingClientRect().width);
        expect(Math.max(...widths) - Math.min(...widths), `width ${width}: lanes lost their equal widths: ${widths.join(', ')}`).toBeLessThan(2);

        const unknown = root.querySelector<HTMLElement>('app-board-column[data-col="unknown"]')!;
        expect(unknown.querySelector('.col-lbl')?.textContent?.trim(), `width ${width}: the lane is not headed with the raw status`).toBe('newer-status');
        const card = unknown.querySelector<HTMLElement>(`[data-chunk="${NEWER_STATUS.chunk_id}"]`);
        expect(card, `width ${width}: the chunk's card is not in the unknown lane`).not.toBeNull();
        const lane = unknown.getBoundingClientRect();
        const box = card!.getBoundingClientRect();
        expect(box.left, `width ${width}: the card starts outside its lane`).toBeGreaterThanOrEqual(lane.left - 0.5);
        expect(box.right, `width ${width}: the card runs past its lane`).toBeLessThanOrEqual(lane.right + 0.5);
      } finally {
        root.remove();
      }
    });
  }
});
