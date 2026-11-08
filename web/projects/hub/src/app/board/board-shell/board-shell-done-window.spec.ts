import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkSummary, KitAsyncStateValue } from 'fleet';
import { chunkCountsView, chunkSummary } from 'fleet/testing';
import { BoardShell } from './board-shell';
import { glanceListGroups } from '../glance/glance-board.model';

/** The DONE column counts the hub-selected rows, rather than the all-time total. */
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

  it('uses a generic heading until the policy is supplied', async () => {
    const el = await render([], 'ready', null);

    expect(head(el, 'done')?.querySelector('.col-lbl')?.textContent?.trim()).toBe('Done');
  });

  it('counts the selected cards, not the all-time total, which its head\'s title carries', async () => {
    const el = await render([done('a'), done('b')], 'ready', 207);

    expect(head(el, 'done')?.querySelector('.n')?.textContent?.trim()).toBe('2');
    expect(head(el, 'done')?.getAttribute('title')).toBe(
      'Server-selected completed work · 207 all time',
    );
    expect(head(el, 'ready')?.hasAttribute('title')).toBe(false);
  });

  it('shows the same server-selected rows as glance despite clock skew and missing timestamps', async () => {
    const chunks = [
      { ...done('old'), completed_at: '2020-01-01T00:00:00Z' },
      { ...done('future'), completed_at: '2099-01-01T00:00:00Z' },
      { ...done('missing'), completed_at: null },
      { ...done('bad'), completed_at: 'invalid' },
    ];
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('layout', 'list');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const desktop = [...el.querySelectorAll<HTMLElement>('[data-testid="list-row"]')].map((row) => row.dataset['chunk']);
    const mobile = glanceListGroups(chunks, [], [], [], [], new Map(), new Map(), new Map()).done.map((row) => row.chunkId);
    expect(desktop).toEqual(mobile);
    expect(mobile).toEqual(chunks.map((chunk) => chunk.chunk_id).sort((a, b) => a.includes('future') ? -1 : b.includes('future') ? 1 : 0));
  });

  it('uses the supplied server policy for the heading and explanation', async () => {
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('donePolicy', { heading: 'Finished', explanation: 'Completed within 2 hours or among the newest 3', cutoffs: [] });
    await fixture.whenStable();
    const heading = head(fixture.nativeElement as HTMLElement, 'done');
    expect(heading?.querySelector('.col-lbl')?.textContent?.trim()).toBe('Finished');
    expect(heading?.getAttribute('title')).toBe('Completed within 2 hours or among the newest 3');
  });

  it('withholds its count while the board loads, and its all-time total until the counts read resolves', async () => {
    const el = await render([], 'loading', null);

    expect(head(el, 'done')?.querySelector('.n')?.textContent?.trim()).toBe('');
    expect(head(el, 'done')?.getAttribute('title')).toBe('Server-selected completed work');
  });
});
