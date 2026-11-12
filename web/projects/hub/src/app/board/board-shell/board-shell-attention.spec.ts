import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { chunkCountsView, chunkSummary } from 'fleet/testing';
import { BoardShell } from './board-shell';

const CHUNKS = [
  chunkSummary({ chunk_id: 'ch_wait1', graph_id: 'gr_1', status: 'waiting_on_human' }),
  chunkSummary({ chunk_id: 'ch_need1', graph_id: 'gr_1', status: 'needs_human' }),
  chunkSummary({ chunk_id: 'ch_pause', graph_id: 'gr_1', status: 'paused' }),
  chunkSummary({ chunk_id: 'ch_need2', graph_id: 'gr_1', status: 'needs_human' }),
  chunkSummary({ chunk_id: 'ch_wait2', graph_id: 'gr_1', status: 'waiting_on_human' }),
];

describe('BoardShell human attention', () => {
  const render = async () => {
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', CHUNKS);
    fixture.componentRef.setInput('state', 'ready');
    await fixture.whenStable();
    return fixture;
  };

  it('combines waiting and paused cards after escalations, preserving order within each lane', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    const cards = [...el.querySelectorAll('[data-col="needs"] [data-testid="chunk-card"]')];
    expect(cards.map((card) => card.getAttribute('data-chunk'))).toEqual([
      'ch_need1', 'ch_need2', 'ch_wait1', 'ch_pause', 'ch_wait2',
    ]);
    expect(cards.map((card) => card.getAttribute('data-status'))).toEqual([
      'needs_human', 'needs_human', 'waiting_on_human', 'paused', 'waiting_on_human',
    ]);
    expect(el.querySelectorAll('[data-testid="chunk-card"]')).toHaveLength(CHUNKS.length);
    expect(el.querySelector('[data-col="waiting"]')).toBeNull();
  });

  it('sums the hub counts for needs, waiting and paused rather than counting visible cards', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    const count = () => el.querySelector('[data-col="needs"] [data-testid="lane-count"]')?.textContent?.trim();
    expect(count()).toBe('');

    fixture.componentRef.setInput('counts', chunkCountsView({ needs_human: 7, waiting_on_human: 4, paused: 2 }));
    await fixture.whenStable();
    expect(count()).toBe('13');

    fixture.componentRef.setInput('counts', chunkCountsView({ needs_human: 0, waiting_on_human: 0, paused: 0 }));
    await fixture.whenStable();
    expect(count()).toBe('0');
  });

  it('keeps distinct lane and paused status glyphs in List and restores the combined column when switching back', async () => {
    const fixture = await render();
    fixture.componentRef.setInput('layout', 'list');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const rows = [...el.querySelectorAll('[data-testid="list-row"]')];
    expect(rows.map((row) => row.getAttribute('data-chunk'))).toEqual([
      'ch_need1', 'ch_need2', 'ch_wait1', 'ch_pause', 'ch_wait2',
    ]);
    expect(rows.map((row) => row.querySelector('[data-testid="list-row-lane"]')?.getAttribute('data-glyph'))).toEqual([
      'needs', 'needs', 'waiting', 'paused', 'waiting',
    ]);

    fixture.componentRef.setInput('layout', 'board');
    await fixture.whenStable();
    expect(el.querySelectorAll('[data-col]')).toHaveLength(5);
    expect(el.querySelectorAll('[data-col="needs"] [data-testid="chunk-card"]')).toHaveLength(5);
    expect(el.querySelector('[data-testid="board-list"]')).toBeNull();
  });

  it('keeps the layout controls without restoring the removed board heading', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.hdr')?.textContent).not.toContain('Chunk board');
    for (const layout of ['board', 'list', 'tracks'])
      expect(el.querySelector(`[data-testid="board-layout-${layout}"]`)).not.toBeNull();
  });
});
