import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { commands, page } from 'vitest/browser';

import type { ChunkSummary } from 'fleet';
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
  terminal: false,
  work_refs: [],
  ...overrides,
});


/**
 * A long current-node name truncates inside its card rather than widening its lane: every
 * lane keeps its equal width inside a 1440px viewport, and the full name stays readable
 * from the card's `title`. Under a real browser — see `blizzard-context:/verification/blizzard/commands/web/shell-sweep.md` §web:shell-sweep.
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
