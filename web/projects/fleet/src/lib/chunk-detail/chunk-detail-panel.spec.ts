import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { vi } from 'vitest';

import type { ChunkDetail } from '../api/hub';
import { ChunkDetailPanel } from './chunk-detail-panel';
import type { WorkItemsState } from './work-items-state';

const ISSUE_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01issue00000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [
    { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42' },
  ],
  history: [],
  artifacts: [],
};

const ROUTED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01routed000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', workspace_id: 'ws_01', environment_ids: ['env_01'] },
};

const WAITING_QUESTION_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01ask00000000000000000000000',
  graph_id: 'gr_1',
  status: 'waiting_on_human',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  questions: [
    {
      question_id: 'qn_01',
      chunk_id: 'ch_01ask00000000000000000000000',
      question: 'Which API style should the endpoint use?',
      options: [],
      epoch: 1,
      runner_id: 'rn_01',
      session_id: 'se_01',
      asked_at: '2026-07-13T00:00:01Z',
      answered: false,
    },
  ],
};

describe('ChunkDetailPanel', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkDetailPanel],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  it('renders all three columns and their sibling components, mounted at once (AC3)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Three columns, all mounted at once: the work item does not cost the operator
    // sight of where the chunk has been or what it produced. Asserted through the
    // regions' labels rather than their CSS classes — a restyle that reshapes the
    // wrapper changes nothing about that guarantee.
    expect(el.querySelector('[aria-label="Work item"]')).not.toBeNull();
    // The node-history and artifacts sections are labelled through their own visible
    // heading (`aria-labelledby`) rather than a second, literal copy of its text
    // (issue #205) — asserted through the reference rather than a duplicated string.
    expect(el.querySelector('[aria-labelledby="chunk-timeline-heading"]')).not.toBeNull();
    expect(el.querySelector('[aria-labelledby="chunk-artifacts-heading"]')).not.toBeNull();

    // Each column's sibling components rendered — the composition wired `detail`
    // (and, below, `workItems`) down to every one of them.
    expect(el.querySelector('[data-testid="detail-id"]')?.textContent?.trim()).toBe(ISSUE_DETAIL.chunk_id);
    expect(el.querySelector('[data-testid="chunk-facts"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-pane"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="history-active"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="artifacts-empty"]')).not.toBeNull();
  });

  it('renders "Node history" exactly once (issue #205)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.outerHTML.match(/Node history/g) ?? []).toHaveLength(1);
  });

  it('orders the work-item column work item, asks/decisions, issues (issue #205)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    fixture.componentRef.setInput('canAnswer', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const workItemSection = el.querySelector('[aria-label="Work item"]')!;
    const order = [
      ...workItemSection.querySelectorAll('[data-testid="chunk-facts"], [data-testid="awaiting-human"], [data-testid="issue-pane"]'),
    ].map((node) => node.getAttribute('data-testid'));
    expect(order).toEqual(['chunk-facts', 'awaiting-human', 'issue-pane']);
  });

  it('links the chunk longname to its dedicated page (issue #205)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const idLink = el.querySelector<HTMLAnchorElement>('[data-testid="detail-id"]');
    expect(idLink?.getAttribute('href')).toBe(`/board/chunk/${ISSUE_DETAIL.chunk_id}`);
  });

  it('forwards workItems down to the issue pane', async () => {
    const workItems: WorkItemsState = {
      status: 'success',
      items: [
        { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42', fetched_at: 't', body: 'reproduces under load', comments: [] },
      ],
    };
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('workItems', workItems);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
  });

  it('surfaces the awaiting-human gate for a parked chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="awaiting-human"]')).not.toBeNull();
  });

  it('emits dismiss when the close button is activated, through the header', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    let closed = false;
    fixture.componentInstance.dismiss.subscribe(() => (closed = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detail-close"]')?.click();
    expect(closed).toBe(true);
  });

  it('emits detach with the chunk id once the operator confirms, through the header', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted: string | undefined;
    fixture.componentInstance.detach.subscribe((chunkId) => (emitted = chunkId));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();

    expect(emitted).toBe(ROUTED_DETAIL.chunk_id);
    confirmSpy.mockRestore();
  });

  it('emits editGraph from the facts column', async () => {
    // `current_node_id: null` is what makes the fixture coherent: a chunk only moves
    // once claimed, so a `not_ready` one stands on no node — and the edit row is gated
    // on both halves of `EditService`'s window, unclaimed and unmoved (issue #271).
    const notReady: ChunkDetail = { ...ROUTED_DETAIL, status: 'not_ready', route: null, current_node_id: null };
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', notReady);
    fixture.componentRef.setInput('canControl', true);
    let emitted: { chunkId: string; graphId: string } | undefined;
    fixture.componentInstance.editGraph.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="graph-input"]')!;
    input.value = 'gr_alt';
    el.querySelector<HTMLButtonElement>('[data-testid="graph-submit"]')?.click();

    expect(emitted).toEqual({ chunkId: notReady.chunk_id, graphId: 'gr_alt' });
  });

  it('emits answerQuestion from the awaiting-human column', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    fixture.componentRef.setInput('canAnswer', true);
    let emitted: { questionId: string; answer: string; chunkId: string } | undefined;
    fixture.componentInstance.answerQuestion.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'rest';
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();

    expect(emitted).toEqual({ questionId: 'qn_01', answer: 'rest', chunkId: WAITING_QUESTION_DETAIL.chunk_id });
  });

  // --- The shared action-error notice (issue #42) -----------------------------
  //
  // One notice serves every operator action in the dock (detach, pause, resume,
  // graph/model edit) — it renders directly off `actionError`, between the header
  // and the columns, regardless of which action produced it.

  it('surfaces a detach error passed down from the container instead of swallowing it', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('actionError', 'chunk ch_01routed000000000000000000 has no live route');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('has no live route');
  });

  it('surfaces a pause error passed down from the container in the shared notice', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('actionError', 'chunk ch_01routed000000000000000000 is not pausable (done)');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('not pausable');
  });

  it('surfaces a graph/model edit error passed down from the container in the shared notice', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status: 'not_ready', route: null });
    fixture.componentRef.setInput('actionError', 'chunk ch_01ready000000000000000000000 has already left not_ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('left not_ready');
  });
});
