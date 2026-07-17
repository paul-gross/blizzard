import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkStatus, ChunkSummary } from '../api/hub';
import { LANES, STATUS_LANE } from '../chunk-lanes';
import { BoardHeader } from './board-header';

const chunk = (id: string, status: ChunkSummary['status']): ChunkSummary => ({
  chunk_id: id,
  graph_id: 'gr_1',
  status,
  current_node_id: 'nd_build',
  pm_pointers: [],
});

describe('BoardHeader', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [BoardHeader],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  const render = async (chunks: ChunkSummary[], connection = 'ok') => {
    const fixture = TestBed.createComponent(BoardHeader);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('connection', connection);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  };

  it('reflects the connection input', async () => {
    const el = await render([], 'reconnecting…');
    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('reconnecting…');
  });

  it('counts the fleet into its lanes, folding the transient and terminal states in', async () => {
    // `delivering` is a running chunk mid-hand-off and `stopped` a terminal one — the
    // board shows each under RUNNING and DONE, and the header must agree with the board
    // rather than invent two more lanes for states an operator does not act on.
    const el = await render([
      chunk('ch_1', 'ready'),
      chunk('ch_2', 'running'),
      chunk('ch_3', 'delivering'),
      chunk('ch_4', 'waiting_on_human'),
      chunk('ch_5', 'needs_human'),
      chunk('ch_6', 'done'),
      chunk('ch_7', 'stopped'),
      chunk('ch_8', 'not_ready'),
    ]);

    const stat = (key: string) => el.querySelector(`[data-testid="stat-${key}"]`)?.textContent?.trim();
    expect(stat('total')).toBe('8');
    expect(stat('ready')).toBe('1');
    expect(stat('notready')).toBe('1');
    expect(stat('running')).toBe('2');
    expect(stat('waiting')).toBe('1');
    expect(stat('needs')).toBe('1');
    expect(stat('done')).toBe('2');
  });

  it('shows one cell per board lane, in the board\'s order — it cannot list a lane the board lacks', async () => {
    // The header is grouped through the same LANES the board columns are built from,
    // so the two cannot drift apart; a lane added there appears in both or neither.
    const el = await render([]);
    const cells = [...el.querySelectorAll('[data-stat]')].map((c) => c.getAttribute('data-stat'));
    expect(cells).toEqual(['total', 'ready', ...LANES.map((l) => l.key)]);
  });

  it('counts every wire status into some cell, so none can go silently missing', async () => {
    // The exhaustive Record in chunk-lanes makes a new status a compile error there;
    // this asserts the consequence — the lanes plus the ready rail account for the
    // whole fleet, so total always equals the sum of the cells beside it.
    const everyStatus = Object.keys(STATUS_LANE) as ChunkStatus[];
    const el = await render(everyStatus.map((s, i) => chunk(`ch_${i}`, s)));

    const value = (key: string) =>
      Number(el.querySelector(`[data-testid="stat-${key}"]`)?.textContent?.trim() ?? '0');
    const lanesAndReady = value('ready') + LANES.reduce((sum, lane) => sum + value(lane.key), 0);
    expect(lanesAndReady).toBe(everyStatus.length);
    expect(value('total')).toBe(everyStatus.length);
  });

  it('rests at zero for an idle fleet', async () => {
    const el = await render([]);
    expect(el.querySelector('[data-testid="stat-total"]')?.textContent?.trim()).toBe('0');
    expect(el.querySelector('[data-testid="stat-needs"]')?.textContent?.trim()).toBe('0');
  });
});
