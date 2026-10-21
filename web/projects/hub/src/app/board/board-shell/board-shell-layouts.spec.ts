import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkSummary, WorkItemsView } from 'fleet';
import { workItemEntry, workRefView, chunkUsageTotalView } from 'fleet/testing';
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


describe('BoardShell layouts', () => {
  const RUNNING = (suffix: string): ChunkSummary => (chunkSummary({
    chunk_id: `ch_01running${suffix}`,
    graph_id: 'gr_1',
    status: 'running',
    current_node_id: 'nd_build',
    work_refs: [workRefView({ source: 'hub', ref: suffix, label: `hub#${suffix}`, web_url: null })],
  }));

  const render = async (
    layout: 'board' | 'list',
    workItems: ReadonlyMap<string, WorkItemsView> = new Map(),
    chunks: readonly ChunkSummary[] = [RUNNING('a')],
  ) => {
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', chunks);
    fixture.componentRef.setInput('state', 'ready');
    fixture.componentRef.setInput('layout', layout);
    fixture.componentRef.setInput('workItems', workItems);
    await fixture.whenStable();
    return fixture;
  };

  it('renders the lane grid by default and the list when asked', async () => {
    const grid = (await render('board')).nativeElement as HTMLElement;
    expect(grid.querySelector('[data-testid="board"]')).not.toBeNull();
    expect(grid.querySelector('[data-testid="board-list"]')).toBeNull();

    TestBed.resetTestingModule();
    const list = (await render('list')).nativeElement as HTMLElement;
    expect(list.querySelector('[data-testid="board"]')).toBeNull();
    expect(list.querySelectorAll('[data-testid="list-row"]')).toHaveLength(1);
  });

  it('lists what wants a person and work in flight, then READY and BACKLOG in their hub order, then DONE', async () => {
    const done: ChunkSummary = { ...RUNNING('b'), status: 'done', terminal: true };
    const ready: ChunkSummary = { ...RUNNING('c'), status: 'ready' };
    const backlog: ChunkSummary = { ...RUNNING('d'), status: 'not_ready' };
    const list = (await render('list', new Map(), [done, backlog, RUNNING('a'), ready])).nativeElement as HTMLElement;

    const rows = [...list.querySelectorAll('[data-testid="list-row"]')];
    expect(rows.map((row) => row.getAttribute('data-chunk'))).toEqual(['ch_01runninga', 'ch_01runningc', 'ch_01runningd', 'ch_01runningb']);
    expect(rows.map((row) => row.querySelector('[data-testid="list-row-lane"]')?.getAttribute('data-glyph'))).toEqual([
      'running',
      'ready',
      'notready',
      'done',
    ]);
  });

  it('emits the layout its switch is set to', async () => {
    const fixture = await render('board');
    const chosen: string[] = [];
    fixture.componentInstance.layoutChange.subscribe((layout) => chosen.push(layout));

    (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="board-layout-list"]')!.click();

    expect(chosen).toEqual(['list']);
  });

  it("leads a card with its chunk's work item titles once they are read", async () => {
    const unread = (await render('board')).nativeElement as HTMLElement;
    expect(unread.querySelector('[data-testid="chunk-title"]')).toBeNull();

    TestBed.resetTestingModule();
    const read = (
      await render('board', new Map([['ch_01runninga', { items: [workItemEntry({ source: 'hub', ref: 'a', title: 'Fix the lease', fetched_at: 't', label: null, body: null, web_url: null })] }]]))
    ).nativeElement as HTMLElement;
    expect(read.querySelector('[data-testid="chunk-title"]')?.textContent?.trim()).toBe('Fix the lease');
  });
});
