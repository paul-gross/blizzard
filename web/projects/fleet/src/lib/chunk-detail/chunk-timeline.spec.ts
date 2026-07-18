import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkDetail } from '../api/hub';
import { ChunkTimeline } from './chunk-timeline';

const REVIEW_FAIL_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01review0000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 2,
  pm_pointers: [],
  history: [
    { from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-07-13T00:00:01Z' },
    { from_node_id: 'nd_review', to_node_id: 'nd_build', choice_name: 'fail', epoch: 2, recorded_at: '2026-07-13T00:00:02Z' },
  ],
  artifacts: [],
};

const COST_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01cost00000000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_review',
  latest_epoch: 2,
  pm_pointers: [],
  history: [
    { from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-07-13T00:00:01Z' },
    { from_node_id: 'nd_review', to_node_id: 'nd_build', choice_name: 'fail', epoch: 2, recorded_at: '2026-07-13T00:00:02Z' },
  ],
  artifacts: [],
  cost: {
    input_tokens: 1200,
    output_tokens: 800,
    cache_read_tokens: 300,
    cache_create_tokens: 100,
    cost_usd: 0.42,
    cost_partial: false,
  },
  // Only the first (nd_build, epoch 1) step recorded usage — the second step
  // (nd_review, epoch 2) has none yet, so its inline usage must stay absent.
  usage: [
    {
      node_id: 'nd_build',
      epoch: 1,
      kind: 'spawn',
      model: 'claude-opus-4-8',
      input_tokens: 1200,
      output_tokens: 800,
      cache_read_tokens: 300,
      cache_create_tokens: 100,
      cost_usd: 0.42,
    },
  ],
};

const PARTIAL_COST_DETAIL: ChunkDetail = {
  ...COST_DETAIL,
  chunk_id: 'ch_01partial00000000000000000000',
  usage: [
    {
      node_id: 'nd_build',
      epoch: 1,
      kind: 'spawn',
      model: 'claude-opus-4-8',
      input_tokens: 100,
      output_tokens: 50,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      cost_usd: null,
    },
  ],
};

const NAMED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01named000000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 1,
  pm_pointers: [],
  history: [
    {
      from_node_id: 'nd_build',
      from_node_name: 'build',
      to_node_id: 'nd_review',
      to_node_name: 'code-review',
      choice_name: 'pass',
      epoch: 1,
      recorded_at: '2026-07-13T00:00:01Z',
    },
  ],
  artifacts: [],
};

describe('ChunkTimeline', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkTimeline],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the review-fail loop (MVP criterion 9/11)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const steps = el.querySelectorAll('[data-testid="history-step"]');
    expect(steps).toHaveLength(2);
    expect(steps[0].querySelector('.nd')?.textContent).toContain('nd_build');
    const failStep = el.querySelector('[data-testid="history-step"][data-choice="fail"]');
    expect(failStep?.querySelector('.nd')?.textContent).toContain('nd_review');
    expect(failStep?.querySelector('[data-testid="history-choice"]')?.textContent).toContain('fail');
    expect(failStep?.querySelector('.jg-to')?.textContent).toContain('nd_build');

    const active = el.querySelector('[data-testid="history-active"]');
    expect(active?.getAttribute('data-choice')).toBe('run');
    expect(active?.querySelector('.nd')?.textContent).toContain('nd_build');
    expect(active?.querySelector('[data-testid="history-active-verb"]')?.textContent).toContain('run');
  });

  it('shows no transitions yet when the chunk has no history and no node in flight', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', {
      ...REVIEW_FAIL_DETAIL,
      status: 'not_ready',
      current_node_id: null,
      history: [],
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="history-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="history"]')).toBeNull();
  });

  it('renders human node names on transitions, keeping the raw id as a tooltip (issue #23)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', NAMED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const step = el.querySelector('[data-testid="history-step"]')!;
    expect(step.querySelector('.nd')?.textContent?.trim()).toBe('build');
    expect(step.querySelector('.jg-to')?.textContent).toContain('code-review');
    expect(step.textContent).not.toContain('nd_');
    expect(step.querySelector('.nd')?.getAttribute('title')).toBe('nd_build');
    expect(step.querySelector('.jg-to')?.getAttribute('title')).toBe('nd_review');
    expect(el.querySelector('[data-testid="history-active"] .nd')?.textContent?.trim()).toBe('review');
  });

  it('falls back to the raw node id when a transition has no resolved name', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const step = el.querySelector('[data-testid="history-step"]')!;
    expect(step.querySelector('.nd')?.textContent?.trim()).toBe('nd_build');
    expect(step.querySelector('.jg-to')?.textContent).toContain('nd_review');
  });

  it("shows each history step's own usage inline, matched by its (node, epoch) — absent for a step with none yet (issue #60)", async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const steps = el.querySelectorAll('[data-testid="history-step"]');
    expect(steps).toHaveLength(2);
    const firstStepUsage = steps[0].querySelector('[data-testid="history-step-usage"]');
    expect(firstStepUsage).not.toBeNull();
    expect(firstStepUsage?.querySelector('[data-testid="history-step-cost"]')?.textContent).toContain('$0.42');
    expect(firstStepUsage?.querySelector('[data-testid="history-step-tokens"]')?.textContent).toContain('2.4k');
    expect(steps[1].querySelector('[data-testid="history-step-usage"]')).toBeNull();
  });

  it("marks a history step's own usage as PARTIAL when its cost was absent (issue #60)", async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', PARTIAL_COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const firstStepUsage = el.querySelectorAll('[data-testid="history-step"]')[0].querySelector(
      '[data-testid="history-step-usage"]',
    );
    expect(firstStepUsage?.querySelector('[data-testid="history-step-cost"]')?.textContent).toContain('~$0.00');
    expect(firstStepUsage?.querySelector('[data-testid="history-step-cost-partial"]')).not.toBeNull();
  });
});
