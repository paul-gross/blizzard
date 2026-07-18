import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkSummary } from '../api/hub';
import { compactRef } from '../compact-ref';
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
    // Five board columns: the not-ready backlog plus the four post-dispatch lanes.
    // READY has no column — ready chunks live in the queue rail (issue #22).
    expect(el.querySelectorAll('[data-col]')).toHaveLength(5);
    expect(el.querySelector('[data-col="notready"]')).toBeTruthy();
    expect(el.querySelector('[data-col="ready"]')).toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('NO CHUNKS');
  });

  it('renders a not-ready chunk in the backlog column with a Promote action that emits its id', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01notready00000000000000000', graph_id: 'gr_1', model: 'claude-opus-4-8', status: 'not_ready', current_node_id: 'nd_build', pm_pointers: [] },
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', model: 'claude-opus-4-8', status: 'running', current_node_id: 'nd_build', pm_pointers: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    let promoted: string | undefined;
    fixture.componentInstance.promote.subscribe((id) => (promoted = id));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The not-ready chunk is a card in its own column, distinct from the ready rail and the
    // running lane; only it carries a Promote button.
    const card = el.querySelector('[data-col="notready"] [data-testid="chunk-card"]');
    expect(card?.getAttribute('data-status')).toBe('not_ready');
    expect(el.querySelectorAll('[data-testid="promote-chunk"]')).toHaveLength(1);
    expect(el.querySelector('[data-col="running"] [data-testid="promote-chunk"]')).toBeNull();

    card?.querySelector<HTMLButtonElement>('[data-testid="promote-chunk"]')?.click();
    expect(promoted).toBe('ch_01notready00000000000000000');
  });

  it('renders one card per non-ready chunk, in its derived-status column, showing status + current node', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01ready0000000000000000000', graph_id: 'gr_1', model: 'claude-opus-4-8', status: 'ready', current_node_id: 'nd_build', pm_pointers: [] },
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', model: 'claude-opus-4-8', status: 'running', current_node_id: 'nd_build', pm_pointers: [] },
      { chunk_id: 'ch_01done00000000000000000000', graph_id: 'gr_1', model: 'claude-opus-4-8', status: 'done', current_node_id: 'done', pm_pointers: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // No empty state once the fleet has chunks. The ready chunk is not a board card —
    // it lives in the queue rail (issue #22) — so only running + done render here.
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="chunk-card"]')).toHaveLength(2);

    // A card carries the derived status and the current node id.
    const running = el.querySelector('[data-col="running"] [data-testid="chunk-card"]');
    expect(running?.querySelector('[data-testid="chunk-status"]')?.textContent).toContain('running');
    expect(running?.querySelector('[data-testid="chunk-node"]')?.textContent).toContain('nd_build');

    // Each non-ready status lands in its own column; the ready chunk never appears as a card.
    expect(el.querySelectorAll('[data-col="running"] [data-testid="chunk-card"]')).toHaveLength(1);
    expect(el.querySelectorAll('[data-col="done"] [data-testid="chunk-card"]')).toHaveLength(1);
    expect(el.querySelector('[data-status="ready"]')).toBeNull();
  });

  it('renders a paused chunk in the WAIT/HUMAN column (issue #46)', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01paused000000000000000000', graph_id: 'gr_1', model: 'claude-opus-4-8', status: 'paused', current_node_id: 'nd_build', pm_pointers: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Paused shares the WAIT/HUMAN column — work stopped pending a human either way.
    const card = el.querySelector('[data-col="waiting"] [data-testid="chunk-card"]');
    expect(card?.getAttribute('data-status')).toBe('paused');
  });

  it('emits the chunk id when a card is activated (fills the detail dock)', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', model: 'claude-opus-4-8', status: 'running', current_node_id: 'nd_build', pm_pointers: [] },
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
        graph_id: 'gr_1', model: 'claude-opus-4-8',
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

  it('names the PM work item as plain text — a card carries no competing link', async () => {
    const chunks: ChunkSummary[] = [
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1', model: 'claude-opus-4-8',
        status: 'running',
        current_node_id: 'nd_build',
        current_node_name: 'build',
        pm_pointers: [
          { source: 'blizzard', ref: '8', label: 'blizzard#8', web_url: 'https://github.com/paul-gross/blizzard/issues/8' },
          { source: 'widget', ref: '9', label: 'widget#9', web_url: 'https://github.com/paul-gross/widget/issues/9' },
        ],
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Both pointers read on the one work-item line; the whole card is a single click
    // target for opening the chunk, so nothing inside it is an anchor competing for
    // that click — the link out to the forge lives in the detail panel.
    const item = el.querySelector('[data-testid="pm-chip"]');
    expect(item?.textContent?.trim()).toBe('blizzard#8 widget#9');
    expect(el.querySelectorAll('[data-testid="chunk-card"] a')).toHaveLength(0);
    // The short chunk id stays visible as the stable handle.
    expect(el.querySelector('[data-testid="chunk-id"]')?.textContent).toContain('C-0000');
  });

  it('degrades to the short chunk id when a chunk has no labeled pointer', async () => {
    const chunks: ChunkSummary[] = [
      // Zero pointers, and a pointer whose URL did not parse (null label) — no chips,
      // the short id carries the identity, and nothing errors. Both are non-ready so
      // they render on the board (ready chunks would live in the rail instead).
      { chunk_id: 'ch_01done00000000000000000000', graph_id: 'gr_1', model: 'claude-opus-4-8', status: 'done', current_node_id: 'done', pm_pointers: [] },
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1', model: 'claude-opus-4-8',
        status: 'running',
        current_node_id: 'nd_build',
        pm_pointers: [{ source: 'blizzard', ref: 'wiki', label: null, web_url: null }],
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="pm-chip"]')).toHaveLength(0);
    expect(el.querySelectorAll('[data-testid="chunk-card"]')).toHaveLength(2);
    const running = el.querySelector('[data-col="running"] [data-testid="chunk-card"]');
    expect(running?.querySelector('[data-testid="chunk-id"]')?.textContent).toContain('C-');
  });

  it('names a chunk by its ULID tail, where the entropy that tells chunks apart lives', () => {
    // A leading slice would print the same timestamp prefix on every card minted in
    // the same millisecond-ish window; the tail is what actually discriminates them.
    expect(compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9')).toBe('C-3YJ9');
    expect(compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YAB')).toBe('C-3YAB');
  });

  it("shows the chunk's derived cost total as a badge on its card (issue #60)", async () => {
    const chunks: ChunkSummary[] = [
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        model: 'claude-opus-4-8',
        status: 'running',
        current_node_id: 'nd_build',
        pm_pointers: [],
        cost: {
          input_tokens: 100,
          output_tokens: 50,
          cache_read_tokens: 0,
          cache_create_tokens: 0,
          cost_usd: 1.23,
          cost_partial: false,
        },
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="card-cost"]')?.textContent).toContain('$1.23');
  });

  it("marks a card's cost badge with the lower-bound prefix when the total is PARTIAL (issue #60)", async () => {
    const chunks: ChunkSummary[] = [
      {
        chunk_id: 'ch_01running000000000000000000',
        graph_id: 'gr_1',
        model: 'claude-opus-4-8',
        status: 'running',
        current_node_id: 'nd_build',
        pm_pointers: [],
        cost: {
          input_tokens: 100,
          output_tokens: 50,
          cache_read_tokens: 0,
          cache_create_tokens: 0,
          cost_usd: 0.1,
          cost_partial: true,
        },
      },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="card-cost"]')?.textContent).toContain('~$0.10');
  });

  it('shows no cost badge for a chunk with zero, non-partial spend', async () => {
    const chunks: ChunkSummary[] = [
      { chunk_id: 'ch_01running000000000000000000', graph_id: 'gr_1', model: 'claude-opus-4-8', status: 'running', current_node_id: 'nd_build', pm_pointers: [] },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="card-cost"]')).toBeNull();
  });
});
