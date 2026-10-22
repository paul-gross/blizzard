import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkCountsView, ChunkStatus, ChunkSummary } from 'fleet';
import { chunkCountsView, chunkUsageTotalView } from 'fleet/testing';
import { BoardShell } from './board-shell';

/** A chunk whose status this build's generated vocabulary does not list, as a newer hub may send. */
describe('BoardShell unknown status lane', () => {
  const chunk = (suffix: string, status: string): ChunkSummary => ({
    awaiting_external_merge: false,
    blocked: null,
    closed_prs: [],
    completed_at: null,
    cost: chunkUsageTotalView({ input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_create_tokens: 0, cost_usd: 0 }),
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
    chunk_id: `ch_01${suffix}`,
    graph_id: 'gr_1',
    status: status as ChunkStatus,
    current_node_id: 'nd_build',
    work_refs: [],
  });

  const COUNTS: ChunkCountsView = chunkCountsView({
    not_ready: 0,
    ready: 0,
    running: 1,
    delivering: 0,
    needs_human: 0,
    done: 0,
    terminal: 0,
    total: 1,
  });

  const render = async (layout: 'board' | 'list', chunks: readonly ChunkSummary[]) => {
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('counts', COUNTS);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('layout', layout);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  };

  const columnKeys = (el: HTMLElement) => [...el.querySelectorAll('app-board-column')].map((col) => col.getAttribute('data-col'));

  it('opens no unknown lane while every chunk carries a status the board knows', async () => {
    const el = await render('board', [chunk('run', 'running')]);

    expect(columnKeys(el)).toEqual(['notready', 'ready', 'waiting', 'needs', 'running', 'done']);
  });

  it('seats a chunk with an unknown status in its own lane, headed and counted by its raw status', async () => {
    const el = await render('board', [chunk('run', 'running'), chunk('rev', 'newer-status')]);

    expect(columnKeys(el)).toEqual(['notready', 'ready', 'waiting', 'needs', 'running', 'done', 'unknown']);
    const lane = el.querySelector<HTMLElement>('app-board-column[data-col="unknown"]')!;
    expect(lane.querySelector('.col-lbl')?.textContent?.trim()).toBe('newer-status');
    expect(lane.querySelector('.n')?.textContent?.trim()).toBe('1');
    expect(lane.querySelector('[data-chunk="ch_01rev"]')).not.toBeNull();
    expect(el.querySelector('app-board-column[data-col="notready"] [data-chunk="ch_01rev"]')).toBeNull();
  });

  it('lists a chunk with an unknown status too, its lane icon named by the raw status', async () => {
    const el = await render('list', [chunk('run', 'running'), chunk('rev', 'newer-status')]);

    const row = el.querySelector<HTMLElement>('[data-testid="list-row"][data-chunk="ch_01rev"]');
    expect(row).not.toBeNull();
    expect(row!.querySelector('[data-testid="list-row-lane"]')?.getAttribute('title')).toBe('newer-status');
  });
});
