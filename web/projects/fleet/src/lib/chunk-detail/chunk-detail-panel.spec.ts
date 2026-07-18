import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import type { ChunkDetail } from '../api/hub';
import { ChunkDetailPanel } from './chunk-detail-panel';
import type { PmItemsState } from './chunk-issue-pane';

const ISSUE_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01issue00000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  pm_pointers: [
    { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42' },
  ],
  history: [],
  artifacts: [],
};

const ROUTED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01routed000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  pm_pointers: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', workspace_id: 'ws_01', environment_ids: ['env_01'] },
};

const WAITING_QUESTION_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01ask00000000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'waiting_on_human',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  pm_pointers: [],
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
      providers: [provideZonelessChangeDetection()],
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
    expect(el.querySelector('[aria-label="Node history"]')).not.toBeNull();
    expect(el.querySelector('[aria-label="Artifacts and asks"]')).not.toBeNull();

    // Each column's sibling components rendered — the composition wired `detail`
    // (and, below, `pmItems`) down to every one of them.
    expect(el.querySelector('[data-testid="detail-id"]')?.textContent?.trim()).toBe(ISSUE_DETAIL.chunk_id);
    expect(el.querySelector('[data-testid="chunk-facts"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-pane"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="history-active"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="artifacts-empty"]')).not.toBeNull();
  });

  it('forwards pmItems down to the issue pane', async () => {
    const pmItems: PmItemsState = {
      status: 'success',
      items: [
        { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42', fetched_at: 't', body: 'reproduces under load', comments: [] },
      ],
    };
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('pmItems', pmItems);
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
    let emitted: string | undefined;
    fixture.componentInstance.detach.subscribe((chunkId) => (emitted = chunkId));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();

    expect(emitted).toBe(ROUTED_DETAIL.chunk_id);
    confirmSpy.mockRestore();
  });

  it('emits editGraph from the facts column', async () => {
    const notReady: ChunkDetail = { ...ROUTED_DETAIL, status: 'not_ready', route: null };
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', notReady);
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
