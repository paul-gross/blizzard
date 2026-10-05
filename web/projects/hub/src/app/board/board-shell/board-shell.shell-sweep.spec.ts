import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import type { ChunkSummary } from 'fleet';
import { BoardShell } from './board-shell';

/**
 * A long current-node name must truncate inside its card rather than widen its lane:
 * a real layout claim jsdom cannot make, since it never lays out the lane grid. Every
 * lane has to keep its equal width inside a 1440px viewport, and the full name stays readable from the card's
 * `title`.
 *
 * Excluded from the default `ng test` run like every `*.shell-sweep.spec.ts` — run it
 * via `npm run shell-sweep`.
 */
const LONG_NODE = 'n'.repeat(120);

const RUNNING: ChunkSummary = {
  chunk_id: 'ch_01longnode000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_long',
  current_node_name: LONG_NODE,
  work_refs: [],
};

async function loadDesignTokens(): Promise<void> {
  const css = await commands.readFile('projects/fleet/src/lib/core/design/tokens.css');
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
}

describe('board shell long node name shell sweep (web:shell-sweep)', () => {
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
});
