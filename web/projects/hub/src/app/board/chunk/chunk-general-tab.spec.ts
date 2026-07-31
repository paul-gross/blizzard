import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { hubApi, WorkItemsState } from 'fleet';

import { ChunkGeneralTab } from './chunk-general-tab';

const DETAIL: hubApi.ChunkDetail = {
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
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders every section off plain inputs, in attention order', async () => {
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', DETAIL);
    fixture.componentRef.setInput('workItems', WORK_ITEMS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const sections = Array.from(el.querySelectorAll('[data-testid^="section-"]')).map((node) =>
      node.getAttribute('data-testid'),
    );
    expect(sections).toEqual(['section-work-item', 'section-issues', 'section-node-history', 'section-asks']);
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
  });

  it('re-emits editGraph from the facts section', async () => {
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

  it('re-emits answerQuestion from the asks section', async () => {
    const waiting: hubApi.ChunkDetail = {
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
    el.querySelector<HTMLButtonElement>('[data-testid="answer-submit"]')?.click();

    expect(emitted).toEqual({ questionId: 'qn_01', answer: 'rest', chunkId: DETAIL.chunk_id });
  });
});
