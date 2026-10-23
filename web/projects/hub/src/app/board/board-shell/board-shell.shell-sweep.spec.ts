import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import type { ChunkStatus, ChunkSummary } from 'fleet';
import { chunkUsageTotalView } from 'fleet/testing';
import { BoardShell } from './board-shell';

const chunkSummary = (overrides: Pick<ChunkSummary, 'chunk_id' | 'graph_id' | 'status'> & Partial<ChunkSummary>): ChunkSummary => ({
  awaiting_external_merge: false,
  blocked: null,
  closed_prs: [],
  completed_at: null,
  cost: chunkUsageTotalView({ input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0, cost_usd: 0 }),
  current_node_id: null,
  current_node_name: null,
  default_effort: null,
  default_harnesses: [],
  default_model: [],
  environment_count: 0,
  landed: false,
  landed_repos: [],
  open_prs: [],
  project_id: null,
  project_name: null,
  project_slug: null,
  promotable: false,
  runner_id: null,
  runner_name: null,
  workspace_id: null,
  terminal: false,
  work_refs: [],
  ...overrides,
});


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
        expect(columns, `width ${width}: the unknown lane did not open beside the six`).toHaveLength(7);
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
