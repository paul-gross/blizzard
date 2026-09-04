import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail } from '../api/hub';
import type { WorkItemsState } from '../chunk-detail';
import { ChunkGeneralTab } from './chunk-general-tab';

const DETAIL: ChunkDetail = {
  chunk_id: 'ch_01general0000000000000000000',
  graph_id: 'gr_1',
  status: 'not_ready',
  current_node_id: null,
  latest_epoch: null,
  work_refs: [{ source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42' }],
  history: [],
  artifacts: [],
};

const WORK_ITEMS: WorkItemsState = {
  status: 'success',
  items: [
    { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42', fetched_at: 't', body: 'reproduces under load', comments: [] },
  ],
};

describe('ChunkGeneralTab', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkGeneralTab],
      // `ChunkTimeline`'s row-activation now renders a `RouterLink` for a multi-graph
      // row's own graph badge — NG0201 without a router provided, even though this
      // suite's own single-graph history never reaches it.
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  it('renders the wrapper handle and every section off plain inputs, in attention order', async () => {
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', DETAIL);
    fixture.componentRef.setInput('workItems', WORK_ITEMS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-general-tab"]')).not.toBeNull();
    const sections = Array.from(el.querySelectorAll('[data-testid^="section-"]')).map((node) =>
      node.getAttribute('data-testid'),
    );
    expect(sections).toEqual([
      'section-work-item',
      'section-issues',
      'section-node-history',
      'section-neighborhood',
      'section-asks',
    ]);
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
  });

  it('defaults workItems to a loading state when the caller supplies none', async () => {
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="issue-loading"]')).not.toBeNull();
  });

  it('re-emits editGraph from the facts section when canControl opts in', async () => {
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', DETAIL);
    fixture.componentRef.setInput('canControl', true);
    let emitted: { chunkId: string; graphId: string } | undefined;
    fixture.componentInstance.editGraph.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="graph-input"]')!;
    input.value = 'gr_alt';
    el.querySelector<HTMLButtonElement>('[data-testid="graph-submit"]')?.click();

    expect(emitted).toEqual({ chunkId: DETAIL.chunk_id, graphId: 'gr_alt' });
  });

  it('withholds the graph edit row when canControl defaults off', async () => {
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-input"]')).toBeNull();
  });

  it('forwards graphLinkBase to the facts section, defaulting to no link', async () => {
    const unlinked = TestBed.createComponent(ChunkGeneralTab);
    unlinked.componentRef.setInput('detail', DETAIL);
    await unlinked.whenStable();
    const unlinkedValue = (unlinked.nativeElement as HTMLElement).querySelector('[data-testid="graph-value"]');
    expect(unlinkedValue?.tagName).toBe('SPAN');

    const linked = TestBed.createComponent(ChunkGeneralTab);
    linked.componentRef.setInput('detail', DETAIL);
    linked.componentRef.setInput('graphLinkBase', ['/graphs']);
    await linked.whenStable();
    const linkedValue = (linked.nativeElement as HTMLElement).querySelector('[data-testid="graph-value"]');
    expect(linkedValue?.tagName).toBe('A');
  });

  it('re-emits answerQuestion from the asks section when canAnswer opts in', async () => {
    const waiting: ChunkDetail = {
      ...DETAIL,
      status: 'waiting_on_human',
      questions: [
        {
          question_id: 'qn_01',
          chunk_id: DETAIL.chunk_id,
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
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', waiting);
    fixture.componentRef.setInput('canAnswer', true);
    let emitted: { questionId: string; answer: string; chunkId: string } | undefined;
    fixture.componentInstance.answerQuestion.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="answer-input"]')!;
    input.value = 'rest';
    input.dispatchEvent(new Event('input'));
    await fixture.whenStable();
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();

    expect(emitted).toEqual({ questionId: 'qn_01', answer: 'rest', chunkId: DETAIL.chunk_id });
  });

  it('re-emits resolveDecision from the asks section when canResolve opts in', async () => {
    const waiting: ChunkDetail = {
      ...DETAIL,
      status: 'waiting_on_human',
      decision: {
        decision_id: 'de_01',
        chunk_id: DETAIL.chunk_id,
        node_id: 'nd_gate',
        node_name: 'approve-gate',
        epoch: 1,
        submitted_at: '2026-07-13T00:00:01Z',
        choices: [
          { name: 'approve', description: 'Ship it.' },
          { name: 'reject', description: 'Send it back.' },
        ],
        transitioned: false,
      },
    };
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', waiting);
    fixture.componentRef.setInput('canResolve', true);
    let emitted: { decisionId: string; choice: string; chunkId: string; struck: readonly string[] } | undefined;
    fixture.componentInstance.resolveDecision.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const buttons = el.querySelectorAll<HTMLButtonElement>('[data-testid="decision-choice"]');
    buttons[1].click(); // reject

    expect(emitted).toEqual({ decisionId: 'de_01', choice: 'reject', chunkId: DETAIL.chunk_id, struck: [] });
  });

  it('emits pickStep when a row in the node-history summary is activated', async () => {
    const withHistory: ChunkDetail = {
      ...DETAIL,
      status: 'running',
      history: [
        {
          choice_name: 'pass',
          epoch: 1,
          from_node_id: 'nd_build',
          from_node_name: 'build',
          to_node_id: 'nd_review',
          to_node_name: 'review',
          recorded_at: '2026-08-09T00:00:00.000Z',
        },
      ],
    };
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', withHistory);
    let emitted: string | null | undefined;
    fixture.componentInstance.pickStep.subscribe((key) => (emitted = key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="history-step"]')?.click();

    expect(emitted).toBe('nd_build:1');
  });

  it('forwards issuePanePlacement to the issue pane, defaulting to center', async () => {
    const loading: WorkItemsState = { status: 'loading', items: [] };

    const centered = TestBed.createComponent(ChunkGeneralTab);
    centered.componentRef.setInput('detail', DETAIL);
    centered.componentRef.setInput('workItems', loading);
    await centered.whenStable();
    expect(
      (centered.nativeElement as HTMLElement).querySelector('[data-testid="issue-loading"]')?.classList.contains('inline'),
    ).toBe(false);

    const inlined = TestBed.createComponent(ChunkGeneralTab);
    inlined.componentRef.setInput('detail', DETAIL);
    inlined.componentRef.setInput('workItems', loading);
    inlined.componentRef.setInput('issuePanePlacement', 'inline');
    await inlined.whenStable();
    expect(
      (inlined.nativeElement as HTMLElement).querySelector('[data-testid="issue-loading"]')?.classList.contains('inline'),
    ).toBe(true);
  });
});
