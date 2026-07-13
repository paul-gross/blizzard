import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkSummary } from '../api/hub';
import { BoardShell } from './board-shell';

describe('BoardShell', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the board shell with all five columns and an empty state', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-shell"]')).toBeTruthy();
    expect(el.querySelectorAll('[data-col]')).toHaveLength(5);
    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('NO CHUNKS');
  });

  it('reflects the connection input in the header', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('connection', 'ok');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('ok');
  });

  it('renders one card per chunk, in its derived-status column, showing status + current node', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01ready0000000000000000000', graph_id: 'gr_1', status: 'ready', current_node_id: 'nd_build', pm_pointers: [] },
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', pm_pointers: [] },
      { chunk_id: 'ch_01done00000000000000000000', graph_id: 'gr_1', status: 'done', current_node_id: 'done', pm_pointers: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // No empty state once the fleet has chunks; one card per chunk.
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="chunk-card"]')).toHaveLength(3);

    // A card carries the derived status and the current node id.
    const running = el.querySelector('[data-col="running"] [data-testid="chunk-card"]');
    expect(running?.querySelector('[data-testid="chunk-status"]')?.textContent).toContain('running');
    expect(running?.querySelector('[data-testid="chunk-node"]')?.textContent).toContain('nd_build');

    // Each derived status lands in its own column: ready, running, done each hold one.
    expect(el.querySelectorAll('[data-col="ready"] [data-testid="chunk-card"]')).toHaveLength(1);
    expect(el.querySelectorAll('[data-col="running"] [data-testid="chunk-card"]')).toHaveLength(1);
    expect(el.querySelectorAll('[data-col="done"] [data-testid="chunk-card"]')).toHaveLength(1);
  });
});
