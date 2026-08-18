import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkDetail } from '../api/hub';
import { formatAbsolute } from '../when';
import { ChunkTimeline } from './chunk-timeline';

const REVIEW_FAIL_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01review0000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 2,
  work_refs: [],
  history: [
    { from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-07-13T00:00:01Z' },
    { from_node_id: 'nd_review', to_node_id: 'nd_build', choice_name: 'fail', epoch: 2, recorded_at: '2026-07-13T00:00:02Z' },
  ],
  artifacts: [],
};

const COST_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01cost00000000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_review',
  latest_epoch: 2,
  work_refs: [],
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
  status: 'running',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 1,
  work_refs: [],
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
  status: 'ready',
  current_node_id: 'nd_t_build',
  current_node_name: 'build',
  latest_epoch: 1,
  work_refs: [],
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

  it('carries the full local datetime as the recency stamp\'s tooltip (issue #175)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const stamp = el.querySelector('[data-testid="history-when"]');
    expect(stamp?.getAttribute('title')).toBe(formatAbsolute('2026-07-13T00:00:01Z'));
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

  it('renders its own "Node history" heading by default (issue #205)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('#chunk-timeline-heading')?.textContent).toBe('Node history');
  });

  it('omits its own heading when a consumer already supplies one, e.g. a wrapping fleet-kit-panel (issue #205)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('heading', false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('#chunk-timeline-heading')).toBeNull();
    expect(el.textContent).not.toContain('Node history');
  });

  it("gives a history step's token count and cost their own fixed-width, nowrap tracks so neither wraps mid-text, and lets the pair itself drop to its own line rather than overflow a tight column (issue #204)", async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // jsdom has no layout engine, so the actual line-count at a given width is
    // unassertable here (proven in the browser instead) — what is assertable is that
    // the CSS shape guaranteeing it is in place: each figure is `white-space: nowrap`
    // (a single line's text can never itself break) inside a track that does not
    // shrink below its own fixed size (`flex-shrink: 0`), and the pair's shared row is
    // allowed to wrap the two *items* — not their text — onto their own line
    // (`flex-wrap: wrap`) instead of overflowing a column too narrow to hold both.
    const tokens = el.querySelector('[data-testid="history-step-tokens"]') as HTMLElement;
    const cost = el.querySelector('[data-testid="history-step-cost"]') as HTMLElement;
    const usageRow = el.querySelector('[data-testid="history-step-usage"]') as HTMLElement;

    const tokensStyle = getComputedStyle(tokens);
    expect(tokensStyle.whiteSpace).toBe('nowrap');
    expect(tokensStyle.flexShrink).toBe('0');
    expect(tokensStyle.flexBasis).not.toBe('auto');
    expect(tokensStyle.flexBasis).not.toBe('0px');

    const costStyle = getComputedStyle(cost);
    expect(costStyle.whiteSpace).toBe('nowrap');
    expect(costStyle.flexShrink).toBe('0');
    expect(costStyle.flexBasis).not.toBe('auto');
    expect(costStyle.flexBasis).not.toBe('0px');

    const usageRowStyle = getComputedStyle(usageRow);
    expect(usageRowStyle.display).toBe('flex');
    expect(usageRowStyle.flexWrap).toBe('wrap');
  });

  it('carries no activation affordance and emits nothing when activatable is false (the default)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', TWO_GRAPH_DETAIL);
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    for (const row of el.querySelectorAll('[data-testid="history-step"], [data-testid="history-active"]')) {
      expect(row.getAttribute('role')).toBeNull();
      expect(row.getAttribute('tabindex')).toBeNull();
    }
    (el.querySelector('[data-testid="history-step"]') as HTMLElement).click();
    expect(emitted).toEqual([]);
  });

  it('makes a transition row and the active row activatable, but never a migration row (D1)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    // TWO_GRAPH_DETAIL's own status is 'ready' (no node in flight) — 'running' gives it
    // an active row too, so both activatable shapes are exercised in one fixture.
    fixture.componentRef.setInput('detail', { ...TWO_GRAPH_DETAIL, status: 'running' });
    fixture.componentRef.setInput('activatable', true);
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const transition = el.querySelector('[data-testid="history-step"]') as HTMLElement;
    expect(transition.getAttribute('role')).toBe('button');
    expect(transition.getAttribute('tabindex')).toBe('0');
    transition.click();
    expect(emitted).toEqual(['nd_s_build:1']);

    const migration = el.querySelector('[data-testid="history-migration-step"]') as HTMLElement;
    expect(migration.getAttribute('role')).toBeNull();
    expect(migration.getAttribute('tabindex')).toBeNull();
    migration.click();
    expect(emitted).toEqual(['nd_s_build:1']); // unchanged — a migration row emits nothing

    const active = el.querySelector('[data-testid="history-active"]') as HTMLElement;
    expect(active.getAttribute('role')).toBe('button');
    active.click();
    expect(emitted).toEqual(['nd_s_build:1', 'nd_t_build:1']);
  });

  it('activates a row by Enter and by Space, not just by click', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('activatable', true);
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const [first, second] = el.querySelectorAll('[data-testid="history-step"]');

    first.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    second.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    expect(emitted).toEqual(['nd_build:1', 'nd_review:2']);
  });

  it('applies the selected class only to the row matching selectedKey', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', { ...TWO_GRAPH_DETAIL, status: 'running' });
    fixture.componentRef.setInput('activatable', true);
    fixture.componentRef.setInput('selectedKey', 'nd_s_build:1');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="history-step"]')?.classList.contains('selected')).toBe(true);
    expect(el.querySelector('[data-testid="history-active"]')?.classList.contains('selected')).toBe(false);
  });

  it('clears the selection by re-activating the already-selected row (review:F6)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('activatable', true);
    fixture.componentRef.setInput('selectedKey', 'nd_build:1');
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    (el.querySelector('[data-testid="history-step"]') as HTMLElement).click();
    expect(emitted).toEqual([null]);
  });

  it('gives the active row no activation affordance while latest_epoch is lagging behind a landed transition into it (review:F11)', async () => {
    // REVIEW_FAIL_DETAIL's own review->build transition already lands epoch 2 at
    // nd_build — the same (node, epoch) latest_epoch still names, the lag window
    // between a transition landing and the next lease's epoch bump.
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('activatable', true);
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const active = el.querySelector('[data-testid="history-active"]') as HTMLElement;
    expect(active.getAttribute('role')).toBeNull();
    expect(active.getAttribute('tabindex')).toBeNull();
    active.click();
    expect(emitted).toEqual([]);
  });

  it('keeps the active row activatable when its epoch is not one a landed transition already claims', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    // TWO_GRAPH_DETAIL's own active row lands post-migration at a node no history
    // transition ever routed into — epoch reuse across the migration is not the lag
    // window (review:F11's own doc comment on `deriveActiveRow`).
    fixture.componentRef.setInput('detail', { ...TWO_GRAPH_DETAIL, status: 'running' });
    fixture.componentRef.setInput('activatable', true);
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const active = el.querySelector('[data-testid="history-active"]') as HTMLElement;
    expect(active.getAttribute('role')).toBe('button');
    active.click();
    expect(emitted).toEqual(['nd_t_build:1']);
  });

  it('keeps every row a listitem of the timeline list, even when activatable makes it a button too (review:F5)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('activatable', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const list = el.querySelector('[data-testid="history"]') as HTMLElement;
    expect(list.tagName).toBe('OL');
    expect(list.children.length).toBeGreaterThan(0);
    for (const li of list.children) {
      // The listitem role stays implicit on every <li> — never overridden — so a
      // screen reader still announces `.timeline` with its real item count.
      expect(li.tagName).toBe('LI');
      expect(li.getAttribute('role')).toBeNull();
    }
    // The activation role lives one level down, on the child the button/keyboard
    // behavior is bound to — proven on a row known to carry a real join key.
    const transitionRow = el.querySelector('[data-testid="history-step"]') as HTMLElement;
    expect(transitionRow.getAttribute('role')).toBe('button');
    expect(transitionRow.parentElement?.tagName).toBe('LI');
    expect(transitionRow.parentElement?.getAttribute('role')).toBeNull();
  });
});
