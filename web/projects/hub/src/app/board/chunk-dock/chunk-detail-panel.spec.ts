import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail, WorkItemsState } from 'fleet';
import { chunkControlStub, chunkDetail, decisionView, questionView, recordingAwaitingHumanActions, workItemEntry } from 'fleet/testing';
import { ChunkDetailPanel } from './chunk-detail-panel';

const ISSUE_DETAIL: ChunkDetail = chunkDetail({
  graph_name: null,
  current_node_name: null,
  chunk_id: 'ch_01issue00000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  pausable: true,
  completable: true,
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [
    { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42' },
  ],
  history: [],
  artifacts: [],
});

const ROUTED_DETAIL: ChunkDetail = chunkDetail({
  graph_name: null,
  current_node_name: null,
  chunk_id: 'ch_01routed000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  pausable: true,
  completable: true,
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', runner_name: null, workspace_id: 'ws_01', environment_ids: ['env_01'] },
});

const WAITING_QUESTION_DETAIL: ChunkDetail = chunkDetail({
  graph_name: null,
  current_node_name: null,
  chunk_id: 'ch_01ask00000000000000000000000',
  graph_id: 'gr_1',
  status: 'waiting_on_human',
  pausable: true,
  completable: true,
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  questions: [
    questionView({
      question_id: 'qn_01',
      chunk_id: 'ch_01ask00000000000000000000000',
      question: 'Which API style should the endpoint use?',
      options: [],
      epoch: 1,
      runner_id: 'rn_01',
      session_id: 'se_01',
      asked_at: '2026-07-13T00:00:01Z',
      answered: false,
    }),
  ],
});

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
    fixture.componentRef.setInput('renderedStatus', ISSUE_DETAIL.status);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // Three columns, all mounted at once: the work item does not cost the operator
    // sight of where the chunk has been or what it produced. Asserted through the
    // regions' labels rather than their CSS classes — a restyle that reshapes the
    // wrapper changes nothing about that guarantee.
    expect(el.querySelector('[aria-label="Work item"]')).not.toBeNull();
    // The node-history and artifacts sections are labelled through their own visible
    // heading (`aria-labelledby`) rather than a second, literal copy of its text —
    // asserted through the reference rather than a duplicated string.
    expect(el.querySelector('[aria-labelledby="chunk-timeline-heading"]')).not.toBeNull();
    expect(el.querySelector('[aria-labelledby="chunk-artifacts-heading"]')).not.toBeNull();

    // Each column's sibling components rendered — the composition wired `detail`
    // (and, below, `workItems`) down to every one of them.
    expect(el.querySelector('[data-testid="detail-id"]')?.textContent?.trim()).toBe(ISSUE_DETAIL.chunk_id);
    expect(el.querySelector('[data-testid="chunk-efficiency"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-pane"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="history-active"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="artifacts-empty"]')).not.toBeNull();
  });

  it('renders "Node history" exactly once (issue #205)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ISSUE_DETAIL.status);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.outerHTML.match(/Node history/g) ?? []).toHaveLength(1);
  });

  it('orders asks, work item, issues, answered questions and decisions without duplicating actions', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', {
      ...WAITING_QUESTION_DETAIL,
      questions: [
        ...WAITING_QUESTION_DETAIL.questions!,
        questionView({ question_id: 'qn_answered', chunk_id: WAITING_QUESTION_DETAIL.chunk_id, question: 'Earlier?', answered: true, answer: 'Yes' }),
      ],
      decision: decisionView({
        decision_id: 'de_01', chunk_id: WAITING_QUESTION_DETAIL.chunk_id, node_id: 'gate', node_name: 'gate',
        choices: [{ name: 'approve', description: 'Approve', requires_checks: false, would_override_red_checks: false }],
        check_results: [], transitioned: false,
      }),
    });
    fixture.componentRef.setInput('renderedStatus', WAITING_QUESTION_DETAIL.status);
    fixture.componentRef.setInput('canAnswer', true);
    fixture.componentRef.setInput('canResolve', true);
    const port = recordingAwaitingHumanActions();
    fixture.componentRef.setInput('awaitingHumanActions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const workItemSection = el.querySelector('[aria-label="Work item"]')!;
    const order = [
      ...workItemSection.querySelectorAll('[data-testid="open-question"], [data-testid="chunk-efficiency"], [data-testid="issue-pane"], [data-testid="answered-question"], [data-testid="open-decision"]'),
    ].map((node) => node.getAttribute('data-testid'));
    expect(order).toEqual(['open-question', 'chunk-efficiency', 'issue-pane', 'answered-question', 'open-decision']);
    expect(el.querySelectorAll('[data-testid="answer-submit"]')).toHaveLength(1);
    expect(el.querySelectorAll('[data-testid="decision-choice"]')).toHaveLength(1);
    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'rest';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')!.click();
    el.querySelector<HTMLButtonElement>('[data-testid="decision-choice"]')!.click();
    await vi.waitFor(() => expect(port.resolves).toHaveLength(1));
    expect(port.answers).toEqual([{ chunkId: WAITING_QUESTION_DETAIL.chunk_id, questionId: 'qn_01', draft: { answer: 'rest' } }]);
    expect(port.resolves).toEqual([
      { chunkId: WAITING_QUESTION_DETAIL.chunk_id, decisionId: 'de_01', choice: 'approve', draft: { note: '' } },
    ]);
  });

  it('links the chunk longname to its dedicated page (issue #205)', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ISSUE_DETAIL.status);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const idLink = el.querySelector<HTMLAnchorElement>('[data-testid="detail-id"]');
    expect(idLink?.getAttribute('href')).toBe(`/board/chunk/${ISSUE_DETAIL.chunk_id}`);
  });

  it('forwards workItems down to the issue pane', async () => {
    const workItems: WorkItemsState = {
      status: 'success',
      items: [
        workItemEntry({ source: 'widget', ref: '42', label: 'widget#42', title: null, web_url: 'https://github.com/acme/widget/issues/42', fetched_at: 't', body: 'reproduces under load', comments: [] }),
      ],
    };
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ISSUE_DETAIL.status);
    fixture.componentRef.setInput('workItems', workItems);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
  });

  it('surfaces the awaiting-human gate for a parked chunk', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    fixture.componentRef.setInput('renderedStatus', WAITING_QUESTION_DETAIL.status);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="awaiting-human"]')).not.toBeNull();
  });

  it('emits dismiss when the close button is activated, through the header', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ISSUE_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ISSUE_DETAIL.status);
    let closed = false;
    fixture.componentInstance.dismiss.subscribe(() => (closed = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="detail-close"]')?.click();
    expect(closed).toBe(true);
  });

  it('fires detach through the control port once the operator confirms in the header', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    const control = chunkControlStub();
    fixture.componentRef.setInput('control', control);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="chunk-actions-menu"]')?.click();
    await fixture.whenStable();
    document.body.querySelector<HTMLButtonElement>('[data-testid="detach-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(control.calls).toEqual([['detach', ROUTED_DETAIL.chunk_id]]);
  });

  it('fires complete through the control port once the operator confirms in the header', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    const control = chunkControlStub();
    fixture.componentRef.setInput('control', control);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="chunk-actions-menu"]')?.click();
    await fixture.whenStable();
    document.body.querySelector<HTMLButtonElement>('[data-testid="complete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(control.calls).toEqual([['complete', ROUTED_DETAIL.chunk_id]]);
  });

  it('fires delete through the control port once the operator confirms in the header', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status: 'not_ready', deletable: true, route: null });
    fixture.componentRef.setInput('renderedStatus', 'not_ready');
    const control = chunkControlStub();
    fixture.componentRef.setInput('control', control);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="chunk-actions-menu"]')?.click();
    await fixture.whenStable();
    document.body.querySelector<HTMLButtonElement>('[data-testid="delete-chunk"]')?.click();
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="confirm-dialog-confirm"]')?.click();

    expect(control.calls).toEqual([['delete', ROUTED_DETAIL.chunk_id]]);
  });

  it('forwards renderedStatus down to the header, over the real status', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', 'paused');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-status"]')?.textContent?.trim()).toBe('paused');
  });

  it('saves an answer from the awaiting-human column through its port', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', WAITING_QUESTION_DETAIL);
    fixture.componentRef.setInput('renderedStatus', WAITING_QUESTION_DETAIL.status);
    fixture.componentRef.setInput('canAnswer', true);
    const port = recordingAwaitingHumanActions();
    fixture.componentRef.setInput('awaitingHumanActions', port.actions);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'rest';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();
    await vi.waitFor(() => expect(port.answers).toHaveLength(1));

    expect(port.answers[0]).toEqual({ chunkId: WAITING_QUESTION_DETAIL.chunk_id, questionId: 'qn_01', draft: { answer: 'rest' } });
  });

  // --- The shared action-error notice -----------------------------
  //
  // One notice serves every operator action in the dock (detach, pause, resume,
  // complete, graph/model edit) — it renders directly off `actionError`, between the
  // header and the columns, regardless of which action produced it.

  it('surfaces a detach error passed down from the container instead of swallowing it', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('actionError', 'chunk ch_01routed000000000000000000 has no live route');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('has no live route');
  });

  it('surfaces a pause error passed down from the container in the shared notice', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('actionError', 'chunk ch_01routed000000000000000000 is not pausable (done)');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('not pausable');
  });

  it('surfaces a complete error passed down from the container in the shared notice', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    fixture.componentRef.setInput('renderedStatus', ROUTED_DETAIL.status);
    fixture.componentRef.setInput('actionError', 'unknown chunk ch_01routed000000000000000000');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('unknown chunk');
  });

  it('surfaces a graph/model edit error passed down from the container in the shared notice', async () => {
    const fixture = TestBed.createComponent(ChunkDetailPanel);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, status: 'not_ready', deletable: true, route: null });
    fixture.componentRef.setInput('renderedStatus', 'not_ready');
    fixture.componentRef.setInput('actionError', 'chunk ch_01ready000000000000000000000 has already left not_ready');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('left not_ready');
  });
});
