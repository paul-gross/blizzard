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

  it('emits the chunk id when a card is activated (opens the detail drawer)', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', pm_pointers: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="chunk-card"] button')?.click();
    expect(selected).toBe('ch_01running000000000000000000');
  });

  it('renders the node name as the visible label with the raw ULID demoted to a tooltip', async () => {
    const chunks: ChunkSummary[] = [
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        status: 'running',
        current_node_id: 'nd_01KXHKVCWZ1000000000000000',
        current_node_name: 'review',
        pm_pointers: [],
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const node = el.querySelector('[data-testid="chunk-node"]');
    expect(node?.textContent?.trim()).toBe('review');
    expect(node?.getAttribute('title')).toBe('nd_01KXHKVCWZ1000000000000000');
  });

  it('renders one linked chip per PM pointer, keeping the short chunk id visible', async () => {
    const chunks: ChunkSummary[] = [
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        status: 'running',
        current_node_id: 'nd_build',
        current_node_name: 'build',
        pm_pointers: [
          { provider: 'github', url: 'https://github.com/paul-gross/blizzard/issues/8', label: 'gh:blizzard#8' },
          { provider: 'github', url: 'https://github.com/paul-gross/widget/issues/9', label: 'gh:widget#9' },
        ],
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const chips = el.querySelectorAll<HTMLAnchorElement>('[data-testid="pm-chip"]');
    expect(chips).toHaveLength(2);
    expect(chips[0].textContent?.trim()).toBe('gh:blizzard#8');
    expect(chips[0].getAttribute('href')).toBe('https://github.com/paul-gross/blizzard/issues/8');
    // The short chunk id stays visible as the stable handle.
    expect(el.querySelector('[data-testid="chunk-id"]')?.textContent).toContain('ch_01running');
  });

  it('degrades to the short chunk id when a chunk has no labeled pointer', async () => {
    const chunks: ChunkSummary[] = [
      // Zero pointers, and a pointer whose URL did not parse (null label) — no chips,
      // the short id carries the identity, and nothing errors.
      { chunk_id: 'ch_01ready0000000000000000000', graph_id: 'gr_1', status: 'ready', current_node_id: 'nd_build', pm_pointers: [] },
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        status: 'running',
        current_node_id: 'nd_build',
        pm_pointers: [{ provider: 'github', url: 'https://github.com/paul-gross/blizzard/wiki', label: null }],
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="pm-chip"]')).toHaveLength(0);
    expect(el.querySelectorAll('[data-testid="chunk-card"]')).toHaveLength(2);
    expect(el.querySelectorAll('[data-testid="chunk-id"]')[1]?.textContent).toContain('ch_01running');
  });
});
