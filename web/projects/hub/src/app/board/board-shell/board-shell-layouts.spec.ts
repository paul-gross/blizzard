import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkSummary, WorkItemsView } from 'fleet';
import { BoardShell } from './board-shell';

describe('BoardShell layouts', () => {
  const RUNNING = (suffix: string): ChunkSummary => ({
    chunk_id: `ch_01running${suffix}`,
    graph_id: 'gr_1',
    status: 'running',
    current_node_id: 'nd_build',
    work_refs: [{ source: 'hub', ref: suffix, label: `hub#${suffix}` }],
  });

  const render = async (layout: 'board' | 'list', workItems: ReadonlyMap<string, WorkItemsView> = new Map()) => {
    await TestBed.configureTestingModule({
      imports: [BoardShell],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(BoardShell);
    fixture.componentRef.setInput('chunks', [RUNNING('a')]);
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
    expect(
      [...list.querySelectorAll('[data-testid="list-section"]')].map((s) => s.getAttribute('data-lane')),
    ).toEqual(['needs', 'waiting', 'running', 'ready', 'notready', 'done']);
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
      await render('board', new Map([['ch_01runninga', { items: [{ source: 'hub', ref: 'a', title: 'Fix the lease', fetched_at: 't', comments: [] }] }]]))
    ).nativeElement as HTMLElement;
    expect(read.querySelector('[data-testid="chunk-title"]')?.textContent?.trim()).toBe('Fix the lease');
  });
});
