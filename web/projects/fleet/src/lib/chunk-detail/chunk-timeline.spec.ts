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

// A chunk whose history spans two graphs (issue #90): a transition in the source graph,
// then a cross-graph migration into the triage graph. Both sides carry resolved names,
// so the timeline must not degrade any step to a raw `nd_`/`gr_` id.
const TWO_GRAPH_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01twograph0000000000000000000',
  graph_id: 'gr_triage',
  model: 'claude-opus-4-8',
  status: 'ready',
  current_node_id: 'nd_t_build',
  current_node_name: 'build',
  latest_epoch: 1,
  pm_pointers: [],
  history: [
    {
      from_node_id: 'nd_s_build',
      from_node_name: 'build',
      to_node_id: 'nd_s_review',
      to_node_name: 'review',
      choice_name: 'pass',
      epoch: 1,
      recorded_at: '2026-07-13T00:00:01Z',
      graph_id: 'gr_src',
      graph_name: 'source',
    },
  ],
  migrations: [
    {
      from_node_id: 'nd_s_review',
      from_node_name: 'review',
      from_graph_id: 'gr_src',
      from_graph_name: 'source',
      to_graph_id: 'gr_triage',
      to_graph_name: 'triage',
      landed_node_id: 'nd_t_build',
      landed_node_name: 'build',
      choice_name: 'migrate',
      model: 'claude-sonnet-5',
      recorded_at: '2026-07-13T00:00:02Z',
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

  it('weaves a cross-graph migration into the timeline, resolving names on both graphs (issue #90)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', TWO_GRAPH_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The source-graph transition still resolves its own node names (no raw-id degradation).
    const transition = el.querySelector('[data-testid="history-step"]')!;
    expect(transition.querySelector('.nd')?.textContent).toContain('build');
    expect(transition.querySelector('.jg-to')?.textContent).toContain('review');

    // The migration renders as its own step: the migrate verdict routing to the target
    // graph's landing node.
    const migration = el.querySelector('[data-testid="history-migration-step"]')!;
    expect(migration).not.toBeNull();
    expect(migration.getAttribute('data-choice')).toBe('migrated');
    expect(migration.querySelector('[data-testid="history-choice"]')?.textContent).toContain('migrate');
    expect(migration.querySelector('.jg-to')?.textContent).toContain('triage/build');

    // A two-graph timeline labels each step with the graph it happened in; neither the
    // transition nor the migration degrades to a raw id.
    expect(el.querySelectorAll('[data-testid="history-graph"]').length).toBeGreaterThan(0);
    const history = el.querySelector('[data-testid="history"]')!;
    expect(history.textContent).not.toContain('nd_');
    expect(history.textContent).not.toContain('gr_');
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
