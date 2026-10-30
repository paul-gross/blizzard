import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkSummary, KitAsyncStateValue } from 'fleet';
import { chunkCountsView, chunkSummary } from 'fleet/testing';
import { BoardShell } from './board-shell';

/** The DONE column holds only the hub's recent window, so it counts and names that window rather than all time. */
describe('BoardShell DONE window', () => {
  const done = (suffix: string): ChunkSummary =>
    chunkSummary({
      completed_at: '2026-07-01T00:00:00+00:00',
      terminal: true,
      chunk_id: `ch_01done${suffix}`,
      graph_id: 'gr_1',
      status: 'done',
      current_node_id: 'done',
    });


  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  const render = async (chunks: readonly ChunkSummary[], state: KitAsyncStateValue, allTimeDone: number | null) => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', state);
    fixture.componentRef.setInput('counts', allTimeDone === null ? null : chunkCountsView({ done: allTimeDone, terminal: allTimeDone }));
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  };

  const head = (el: HTMLElement, column: string) => el.querySelector(`[data-col="${column}"] .b-col-head`);

  it('labels its column by the window the hub keeps', async () => {
    const el = await render([], 'ready', null);

    expect(head(el, 'done')?.querySelector('.col-lbl')?.textContent?.trim()).toBe('DONE MAX(48H, 10)');
  });

  it('counts the cards its window shows, not the all-time total, which its head\'s title carries', async () => {
    const el = await render([done('a'), done('b')], 'ready', 207);

    expect(head(el, 'done')?.querySelector('.n')?.textContent?.trim()).toBe('2');
    expect(head(el, 'done')?.getAttribute('title')).toBe(
      'Whichever is more: everything finished in the last 48h, or the 10 most recent · 207 all time',
    );
    expect(head(el, 'ready')?.hasAttribute('title')).toBe(false);
  });

  it('withholds its count while the board loads, and its all-time total until the counts read resolves', async () => {
    const el = await render([], 'loading', null);

    expect(head(el, 'done')?.querySelector('.n')?.textContent?.trim()).toBe('');
    expect(head(el, 'done')?.getAttribute('title')).toBe('Whichever is more: everything finished in the last 48h, or the 10 most recent');
  });
});
