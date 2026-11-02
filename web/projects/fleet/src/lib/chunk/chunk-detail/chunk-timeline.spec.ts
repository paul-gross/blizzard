import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';

import type { ChunkDetail } from '../../api/hub';
import { formatAbsolute } from '../../core/when';
import { chunkDetail, chunkUsageTotalView, chunkUsageView, transitionView } from 'fleet/testing';
import { ChunkTimeline } from './chunk-timeline';

/** A step usage cell's session efficiency cost, part by part. */
function efficiencyParts(usage: Element | null | undefined): string[] {
  return [...(usage?.querySelectorAll('[data-testid="history-step-efficiency"] .part') ?? [])].map((p) => p.textContent ?? '');
}

const REVIEW_FAIL_HISTORY = [
  transitionView({ from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-07-13T00:00:01Z', from_node_name: null, to_node_name: null, graph_id: 'gr_1', graph_name: null }),
  transitionView({ from_node_id: 'nd_review', to_node_id: 'nd_build', choice_name: 'fail', epoch: 2, recorded_at: '2026-07-13T00:00:02Z', from_node_name: null, to_node_name: null, graph_id: 'gr_1', graph_name: null }),
];

const REVIEW_FAIL_DETAIL: ChunkDetail = chunkDetail({
  chunk_id: 'ch_01review0000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 2,
  work_refs: [],
  history: REVIEW_FAIL_HISTORY,
  artifacts: [],
  graph_name: null, current_node_name: null,
});

const COST_DETAIL: ChunkDetail = chunkDetail({
  chunk_id: 'ch_01cost00000000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_review',
  latest_epoch: 2,
  work_refs: [],
  history: REVIEW_FAIL_HISTORY,
  artifacts: [],
  cost: chunkUsageTotalView({
    input_tokens: 1200,
    output_tokens: 800,
    cache_read_tokens: 300,
    cache_create_tokens: 100,
    cost_usd: 0.42,
  }),
  // Only the first (nd_build, epoch 1) step recorded usage — the second step
  // (nd_review, epoch 2) has none yet, so its inline usage must stay absent.
  usage: [
    chunkUsageView({
      node_id: 'nd_build',
      epoch: 1,
      kind: 'spawn',
      model: 'claude-opus-4-8',
      input_tokens: 1200,
      output_tokens: 800,
      cache_read_tokens: 300,
      cache_create_tokens: 100,
      cost_usd: 0.42,
    }),
  ],
  graph_name: null, current_node_name: null,
});

const PARTIAL_COST_DETAIL: ChunkDetail = {
  ...COST_DETAIL,
  chunk_id: 'ch_01partial00000000000000000000',
  usage: [
    chunkUsageView({
      node_id: 'nd_build',
      epoch: 1,
      kind: 'spawn',
      model: 'claude-opus-4-8',
      input_tokens: 100,
      output_tokens: 50,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      cost_usd: null,
      cost_partial: true,
    }),
  ],
};

// A subscription step: no billed cost, only a runner-reported estimate — a row carrying
// only an estimate is not partial, so this step reads not-partial, unlike
// PARTIAL_COST_DETAIL above.
const ESTIMATE_COST_DETAIL: ChunkDetail = {
  ...COST_DETAIL,
  chunk_id: 'ch_01estimate0000000000000000000',
  usage: [
    chunkUsageView({
      node_id: 'nd_build',
      epoch: 1,
      kind: 'spawn',
      model: 'claude-opus-4-8',
      input_tokens: 900,
      output_tokens: 400,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      cost_usd: null,
      estimated_cost_usd: 0.07,
    }),
  ],
};

const NAMED_DETAIL: ChunkDetail = chunkDetail({
  chunk_id: 'ch_01named000000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 1,
  work_refs: [],
  history: [
    transitionView({
      from_node_id: 'nd_build',
      from_node_name: 'build',
      to_node_id: 'nd_review',
      to_node_name: 'code-review',
      choice_name: 'pass',
      epoch: 1,
      recorded_at: '2026-07-13T00:00:01Z',
      graph_id: 'gr_1',
      graph_name: null,
    }),
  ],
  artifacts: [],
  graph_name: null,
});

// A chunk whose history spans two graphs: a transition in the source graph,
// then a cross-graph migration into the triage graph. Both sides carry resolved names,
// so the timeline must not degrade any step to a raw `nd_`/`gr_` id.
const TWO_GRAPH_DETAIL: ChunkDetail = chunkDetail({
  chunk_id: 'ch_01twograph0000000000000000000',
  graph_id: 'gr_triage',
  status: 'ready',
  current_node_id: 'nd_t_build',
  current_node_name: 'build',
  latest_epoch: 1,
  work_refs: [],
  history: [
    transitionView({
      from_node_id: 'nd_s_build',
      from_node_name: 'build',
      to_node_id: 'nd_s_review',
      to_node_name: 'review',
      choice_name: 'pass',
      epoch: 1,
      recorded_at: '2026-07-13T00:00:01Z',
      graph_id: 'gr_src',
      graph_name: 'source',
    }),
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
      epoch: 2,
      recorded_at: '2026-07-13T00:00:02Z',
      source: null,
    },
  ],
  artifacts: [],
  graph_name: null,
});

const FORENSICS_DETAIL: ChunkDetail = chunkDetail({
  chunk_id: 'ch_01forensics000000000000000000',
  graph_id: 'gr_1',
  status: 'done',
  current_node_id: 'nd_done',
  latest_epoch: 2,
  work_refs: [],
  artifacts: [],
  history: [
    transitionView({ from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-07-13T00:00:01Z', from_node_name: null, to_node_name: null, graph_id: 'gr_1', graph_name: null }),
  ],
  bounces: [{ cause: 'failure', envelope: '{"cause":"failure","detail":"hub node `deliver` routed `failure` to `resolve` \\u2014 delivery incomplete"}', recorded_at: '2026-07-13T00:00:02Z', withdrawn: false, rework: false }],
  restarts: [
    {
      epoch: 2,
      graph_id: 'gr_1',
      from_node_id: 'nd_review',
      from_node_name: 'review',
      to_node_id: 'nd_build',
      to_node_name: 'build',
      restarted_by: 'ops@example.test',
      recorded_at: '2026-07-13T00:00:03Z',
      decision_id: null,
      note: null,
      from_graph_id: null,
      from_graph_name: null,
      graph_name: null,
    },
  ],
  graph_name: null, current_node_name: null,
});

describe('ChunkTimeline', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkTimeline],
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([
          { path: 'board', children: [{ path: 'chunk/:id', children: [] }] },
          { path: 'graphs/:id', children: [] },
        ]),
      ],
    }).compileComponents();
  });

  it('renders a bounce and a restart in time order, keyless and inert', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', FORENSICS_DETAIL);
    fixture.componentRef.setInput('activatable', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const picks: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((k) => picks.push(k));

    const ids = [...el.querySelectorAll('[data-testid^="history-"][data-testid$="step"]')].map((n) => n.getAttribute('data-testid'));
    expect(ids).toEqual(['history-step', 'history-bounce-step', 'history-restart-step']);

    const bounce = el.querySelector<HTMLElement>('[data-testid="history-bounce-step"]')!;
    expect(bounce.getAttribute('title')).toBe('hub node deliver routed failure to resolve — delivery incomplete');
    expect(bounce.querySelector('[data-testid="history-bounce-reason"]')?.textContent).toBe('hub node deliver routed failure to resolve — delivery incomplete');
    expect(bounce.querySelector('svg.bounce-icon')).not.toBeNull();
    expect(bounce.querySelector('.att')?.textContent).not.toContain('\u21A9');
    expect(bounce.getAttribute('data-choice')).toBe('bounced');
    expect(bounce.querySelector('[data-testid="history-choice"]')?.textContent).toContain('failure');
    expect(bounce.querySelector('.jg-to')).toBeNull();

    const restart = el.querySelector<HTMLElement>('[data-testid="history-restart-step"]')!;
    expect(restart.querySelector('.nd')?.textContent).toContain('review');
    expect(restart.querySelector('.jg-to')?.textContent).toContain('build');
    expect(restart.querySelector('[data-testid="history-actor"]')?.textContent).toContain('ops@example.test');

    for (const row of [bounce, restart]) {
      expect(row.getAttribute('role')).toBeNull();
      expect(row.getAttribute('tabindex')).toBeNull();
      row.click();
    }
    expect(picks).toEqual([]);
  });

  it('reads a poll-timeout envelope and an unparseable one as plain text', async () => {
    const detail: ChunkDetail = chunkDetail({
      ...FORENSICS_DETAIL,
      bounces: [
        { cause: 'poll-timeout', envelope: '{"cause":"poll-timeout","detail":"no verdict within 600s"}', recorded_at: '2026-07-13T00:00:02Z', withdrawn: false, rework: false },
        { cause: 'unknown', envelope: 'not json {', recorded_at: '2026-07-13T00:00:03Z', withdrawn: false, rework: false },
      ],
      restarts: [],
      graph_name: null, current_node_name: null,
});
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', detail);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const bounces = [...el.querySelectorAll<HTMLElement>('[data-testid="history-bounce-step"]')];
    expect(bounces.map((b) => b.querySelector('[data-testid="history-bounce-reason"]')?.textContent)).toEqual([
      'no verdict within 600s',
      'not json {',
    ]);
    expect(bounces.map((b) => b.getAttribute('title'))).toEqual(['no verdict within 600s', 'not json {']);
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

  it("shows each history step's own session efficiency cost inline, matched by its (node, epoch) — absent for a step with none yet", async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const steps = el.querySelectorAll('[data-testid="history-step"]');
    expect(steps).toHaveLength(2);
    const firstStepUsage = steps[0].querySelector('[data-testid="history-step-usage"]');
    expect(firstStepUsage).not.toBeNull();
    expect(efficiencyParts(firstStepUsage)).toEqual(['$0.42', '· 1.30kI', '/ 800O', '× 81.3%']);
    expect(steps[1].querySelector('[data-testid="history-step-usage"]')).toBeNull();
  });

  it("marks a history step's own cost partial with `+` when its cost was absent", async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', PARTIAL_COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const firstStepUsage = el.querySelectorAll('[data-testid="history-step"]')[0].querySelector(
      '[data-testid="history-step-usage"]',
    );
    expect(efficiencyParts(firstStepUsage)).toEqual(['$0.00+', '· 100I', '/ 50O', '× 100%']);
    expect(firstStepUsage?.querySelector('[data-testid="history-step-efficiency"]')?.getAttribute('title')).toContain('session efficiency cost: $0.00+');
    expect(el.querySelector('.partial-badge')).toBeNull();
  });

  it("renders a step's own estimate folded into its one cost figure, marked ~, and not-partial for an estimate-only row", async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', ESTIMATE_COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const firstStepUsage = el.querySelectorAll('[data-testid="history-step"]')[0].querySelector(
      '[data-testid="history-step-usage"]',
    );
    expect(efficiencyParts(firstStepUsage)[0]).toBe('~$0.07');
  });

  it('renders no ~ marker on a step with no estimate', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const firstStepUsage = el.querySelectorAll('[data-testid="history-step"]')[0].querySelector(
      '[data-testid="history-step-usage"]',
    );
    expect(efficiencyParts(firstStepUsage)[0]).toBe('$0.42');
  });




  it('renders no harness affordance for a step whose usage recorded none (blizzard#441)', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="history-step-harness"]')).toBeNull();
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



  it('links a keyed row to the chunk detail page’s Node history tab when activatable is false (the default), the row itself the anchor, emitting nothing', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', { ...TWO_GRAPH_DETAIL, status: 'running' });
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    for (const row of el.querySelectorAll('[data-testid="history-step"], [data-testid="history-active"]')) {
      expect(row.getAttribute('role')).toBeNull();
      expect(row.getAttribute('tabindex')).toBeNull();
    }

    const transitionLink = el.querySelector('a[data-testid="history-step"]') as HTMLAnchorElement;
    expect(transitionLink.getAttribute('href')).toBe(
      `/board/chunk/${TWO_GRAPH_DETAIL.chunk_id}?tab=node-history&step=nd_s_build:1`,
    );
    const activeLink = el.querySelector('a[data-testid="history-active"]') as HTMLAnchorElement;
    expect(activeLink.getAttribute('href')).toBe(
      `/board/chunk/${TWO_GRAPH_DETAIL.chunk_id}?tab=node-history&step=nd_t_build:1`,
    );
    expect(el.querySelector('a[data-testid="history-migration-step"]')).toBeNull();

    const authoredFixture = TestBed.createComponent(ChunkTimeline);
    authoredFixture.componentRef.setInput('detail', {
      ...TWO_GRAPH_DETAIL,
      migrations: [{ ...TWO_GRAPH_DETAIL.migrations![0], source: 'authored-edge' }],
    });
    await authoredFixture.whenStable();
    const migrationLink = (authoredFixture.nativeElement as HTMLElement).querySelector(
      'a[data-testid="history-migration-step"]',
    ) as HTMLAnchorElement;
    expect(migrationLink.getAttribute('href')).toBe(
      `/board/chunk/${TWO_GRAPH_DETAIL.chunk_id}?tab=node-history&step=nd_s_review:2`,
    );

    (el.querySelector('[data-testid="history-step"]') as HTMLElement).click();
    expect(emitted).toEqual([]);
  });

  it('keeps a multi-graph row’s graph badge plain text inside the row link, so no anchor nests another', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', TWO_GRAPH_DETAIL);
    fixture.componentRef.setInput('graphLinkBase', ['/graphs']);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rowLink = el.querySelector('a[data-testid="history-step"]') as HTMLAnchorElement;
    expect(rowLink.getAttribute('href')).toBe(
      `/board/chunk/${TWO_GRAPH_DETAIL.chunk_id}?tab=node-history&step=nd_s_build:1`,
    );
    const graphBadge = rowLink.querySelector('[data-testid="history-graph"]');
    expect(graphBadge?.tagName).toBe('SPAN');
    expect(rowLink.querySelector('a')).toBeNull();
  });

  it("labels its heading with the chunk's graph, linked under graphLinkBase and plain text without it", async () => {
    const detail = { ...REVIEW_FAIL_DETAIL, graph_id: 'gr_01KXV4DP7T7NKBE8GJJ1GXG857', graph_name: 'glacier', graph_created_at: '2026-07-19T10:00:00Z' };
    const plain = TestBed.createComponent(ChunkTimeline);
    plain.componentRef.setInput('detail', detail);
    await plain.whenStable();
    const plainLabel = (plain.nativeElement as HTMLElement).querySelector('[data-testid="node-history-graph"]');
    expect(plainLabel?.tagName).toBe('SPAN');
    expect(plainLabel?.textContent?.trim()).toBe('G-G857#glacier-20260719');

    const linked = TestBed.createComponent(ChunkTimeline);
    linked.componentRef.setInput('detail', detail);
    linked.componentRef.setInput('graphLinkBase', ['/graphs']);
    await linked.whenStable();
    const link = (linked.nativeElement as HTMLElement).querySelector('[data-testid="node-history-graph"]');
    expect(link?.getAttribute('href')).toBe('/graphs/gr_01KXV4DP7T7NKBE8GJJ1GXG857');
  });

  it('builds the link under a custom linkBase, the same contract chunk-artifacts.ts establishes', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('linkBase', ['/mobile', 'chunk']);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const link = el.querySelector('a[data-testid="history-step"]') as HTMLAnchorElement;
    expect(link.getAttribute('href')).toBe(`/mobile/chunk/${REVIEW_FAIL_DETAIL.chunk_id}?tab=node-history&step=nd_build:1`);
  });

  it('takes focus and navigates on its own — an anchor’s own behavior, not a re-implemented one', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const router = TestBed.inject(Router);

    const link = el.querySelector('a[data-testid="history-step"]') as HTMLAnchorElement;
    expect(link.tagName).toBe('A');
    link.focus();
    expect(document.activeElement).toBe(link);

    link.click();
    await fixture.whenStable();
    expect(router.url).toBe(`/board/chunk/${REVIEW_FAIL_DETAIL.chunk_id}?tab=node-history&step=nd_build:1`);
    expect(emitted).toEqual([]);
  });

  it('navigates when the node name, target, or timestamp of an authored-edge migration row is clicked', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', {
      ...TWO_GRAPH_DETAIL,
      migrations: [{ ...TWO_GRAPH_DETAIL.migrations![0], source: 'authored-edge' }],
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const router = TestBed.inject(Router);
    const expected = `/board/chunk/${TWO_GRAPH_DETAIL.chunk_id}?tab=node-history&step=nd_s_review:2`;
    const row = el.querySelector('[data-testid="history-migration-step"]') as HTMLElement;

    for (const selector of ['.nd', '.jg-to', '.ts']) {
      await router.navigateByUrl('/board/chunk/other');
      (row.querySelector(selector) as HTMLElement).click();
      await fixture.whenStable();
      expect(router.url, selector).toBe(expected);
    }
  });

  it('renders a null-keyed row inert — no anchor, no tabindex, no hover-keyed class (a non-worker migration or an active row with no epoch yet)', async () => {
    const migrationFixture = TestBed.createComponent(ChunkTimeline);
    migrationFixture.componentRef.setInput('detail', { ...TWO_GRAPH_DETAIL, status: 'ready' });
    await migrationFixture.whenStable();
    const migrationEl = migrationFixture.nativeElement as HTMLElement;

    const migration = migrationEl.querySelector('[data-testid="history-migration-step"]') as HTMLElement;
    expect(migration.tagName).toBe('DIV');
    expect(migration.getAttribute('tabindex')).toBeNull();
    expect(migration.classList.contains('keyed')).toBe(false);

    // REVIEW_FAIL_DETAIL's own active row lands in the lag window (below) —
    // a landed transition already claims (nd_build, epoch 2), so its own key is null too.
    const activeFixture = TestBed.createComponent(ChunkTimeline);
    activeFixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await activeFixture.whenStable();
    const activeEl = activeFixture.nativeElement as HTMLElement;

    const active = activeEl.querySelector('[data-testid="history-active"]') as HTMLElement;
    expect(active.tagName).toBe('DIV');
    expect(active.getAttribute('tabindex')).toBeNull();
    expect(active.classList.contains('keyed')).toBe(false);
  });

  it('draws no link and no route change for the General tab’s own activatable rows — they still just emit pickStep', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('activatable', true);
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const router = TestBed.inject(Router);
    const startUrl = router.url;

    expect(el.querySelector('a[data-testid]')).toBeNull();
    const transition = el.querySelector('[data-testid="history-step"]') as HTMLElement;
    expect(transition.classList.contains('keyed')).toBe(true);
    transition.click();
    expect(emitted).toEqual(['nd_build:1']);
    expect(router.url).toBe(startUrl);
  });

  it('makes a transition and active row activatable while a non-worker migration stays inert', async () => {
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
    expect(transition.tagName).toBe('BUTTON');
    transition.click();
    expect(emitted).toEqual(['nd_s_build:1']);

    const migration = el.querySelector('[data-testid="history-migration-step"]') as HTMLElement;
    expect(migration.tagName).toBe('DIV');
    migration.click();
    expect(emitted).toEqual(['nd_s_build:1']);

    const active = el.querySelector('[data-testid="history-active"]') as HTMLElement;
    expect(active.tagName).toBe('BUTTON');
    active.click();
    expect(emitted).toEqual(['nd_s_build:1', 'nd_t_build:1']);
  });

  it('makes an activatable row a native button, so Enter and Space activate it without a key handler', async () => {
    const fixture = TestBed.createComponent(ChunkTimeline);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('activatable', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    for (const row of el.querySelectorAll('[data-testid="history-step"]')) {
      expect(row.tagName).toBe('BUTTON');
      expect(row.getAttribute('type')).toBe('button');
    }
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
    // window (`deriveActiveRow`'s own doc comment).
    fixture.componentRef.setInput('detail', { ...TWO_GRAPH_DETAIL, status: 'running' });
    fixture.componentRef.setInput('activatable', true);
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const active = el.querySelector('[data-testid="history-active"]') as HTMLElement;
    expect(active.tagName).toBe('BUTTON');
    active.click();
    expect(emitted).toEqual(['nd_t_build:1']);
  });

  it('keeps every row a listitem of the timeline list, even when activatable makes it a button too', async () => {
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
    expect(transitionRow.tagName).toBe('BUTTON');
    expect(transitionRow.parentElement?.tagName).toBe('LI');
    expect(transitionRow.parentElement?.getAttribute('role')).toBeNull();
  });
});
