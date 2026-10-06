import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail } from '../../api/hub';
import { ChunkTimelineSelection } from './chunk-timeline-selection';

const COST_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01cost00000000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 2,
  work_refs: [],
  history: [
    {
      from_node_id: 'nd_build',
      from_node_name: 'build',
      to_node_id: 'nd_review',
      to_node_name: 'review',
      choice_name: 'pass',
      epoch: 1,
      recorded_at: '2026-07-13T00:00:01Z',
    },
  ],
  artifacts: [],
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

// A subscription step: no billed cost, only a runner-reported estimate — a row
// carrying only an estimate is not partial, so this step's own line 3 shows no
// lower-bound title.
const ESTIMATE_DETAIL: ChunkDetail = {
  ...COST_DETAIL,
  chunk_id: 'ch_01estimate0000000000000000000',
  usage: [
    {
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
    },
  ],
};

// A chunk whose history spans two graphs — the only shape that grows a graph
// badge to link (`multiGraph()`).
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
      epoch: 2,
      recorded_at: '2026-07-13T00:00:02Z',
    },
  ],
  artifacts: [],
};

const FORENSICS_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01forensics000000000000000000',
  graph_id: 'gr_1',
  status: 'done',
  current_node_id: 'nd_done',
  latest_epoch: 2,
  work_refs: [],
  artifacts: [],
  history: [
    { from_node_id: 'nd_build', to_node_id: 'nd_review', choice_name: 'pass', epoch: 1, recorded_at: '2026-07-13T00:00:01Z' },
  ],
  bounces: [{ cause: 'malformed-result', envelope: '{"raw":true}', recorded_at: '2026-07-13T00:00:02Z' }],
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
    },
  ],
};

describe('ChunkTimelineSelection', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkTimelineSelection],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  it('renders a bounce and a restart in time order, keyless and inert', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', FORENSICS_DETAIL);
    
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const picks: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((k) => picks.push(k));

    const ids = [...el.querySelectorAll('[data-testid^="selection-"][data-testid$="step"]')].map((n) => n.getAttribute('data-testid'));
    expect(ids).toEqual(['selection-step', 'selection-bounce-step', 'selection-restart-step']);

    const bounce = el.querySelector<HTMLElement>('[data-testid="selection-bounce-step"]')!;
    expect(bounce.getAttribute('title')).toBe('{"raw":true}');
    expect(bounce.getAttribute('data-choice')).toBe('bounced');
    expect(bounce.querySelector('[data-testid="selection-choice"]')?.textContent).toContain('malformed-result');
    expect(bounce.querySelector('.jg-to')).toBeNull();

    const restart = el.querySelector<HTMLElement>('[data-testid="selection-restart-step"]')!;
    expect(restart.querySelector('.nd')?.textContent).toContain('review');
    expect(restart.querySelector('.jg-to')?.textContent).toContain('build');
    expect(restart.querySelector('[data-testid="selection-actor"]')?.textContent).toContain('ops@example.test');

    for (const row of [bounce, restart]) {
      expect(row.getAttribute('role')).toBeNull();
      expect(row.getAttribute('tabindex')).toBeNull();
      row.click();
    }
    expect(picks).toEqual([]);
  });

  it('renders the three-line row layout: identity, verdict/routing/when, then usage', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const step = el.querySelector('[data-testid="selection-step"]')!;
    expect(step.querySelector('.line1 .nd')?.textContent).toContain('build');
    expect(step.querySelector('[data-testid="selection-choice"]')?.textContent).toContain('pass');
    expect(step.querySelector('.jg-to')?.textContent).toContain('review');
    const usage = step.querySelector('[data-testid="selection-step-usage"]');
    expect(usage).not.toBeNull();
    expect(usage?.querySelector('[data-testid="selection-step-tokens"]')?.textContent).toContain('2.4k');
    expect(usage?.querySelector('[data-testid="selection-step-cost"]')?.textContent).toContain('$0.42');
  });

  it('omits line 3 for a step with no matched usage yet, including the in-flight row', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', { ...COST_DETAIL, usage: [] });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="selection-step-usage"]')).toBeNull();
    const active = el.querySelector('[data-testid="selection-active"]')!;
    expect(active).not.toBeNull();
    expect(active.querySelector('[data-testid="selection-step-usage"]')).toBeNull();
  });

  it("renders a step's own estimate folded into its one cost figure, marked ~, with no lower-bound title for an estimate-only row", async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', ESTIMATE_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const usage = el.querySelector('[data-testid="selection-step-usage"]')!;
    expect(usage.querySelector('[data-testid="selection-step-cost"]')?.textContent?.trim()).toBe('~$0.07');
    expect(usage.querySelector('[data-testid="selection-step-cost"]')?.hasAttribute('title')).toBe(false);
  });

  it('links a multi-graph row\'s own graph badge when graphLinkBase is set', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', TWO_GRAPH_DETAIL);
    fixture.componentRef.setInput('graphLinkBase', ['/graphs']);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const link = el.querySelector<HTMLAnchorElement>('[data-testid="selection-graph"]');
    expect(link).not.toBeNull();
    expect(link?.tagName).toBe('A');
    expect(link?.getAttribute('href')).toBe('/graphs/gr_src');
  });

  it('renders the graph badge as plain text, not a link, when graphLinkBase is unset', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', TWO_GRAPH_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const badge = el.querySelector('[data-testid="selection-graph"]');
    expect(badge).not.toBeNull();
    expect(badge?.tagName).not.toBe('A');
  });

  it('emits pickStep on activation and clears it by re-activating the selected row', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const step = el.querySelector('[data-testid="selection-step"]') as HTMLElement;
    step.click();
    expect(emitted).toEqual(['nd_build:1']);

    fixture.componentRef.setInput('selectedKey', 'nd_build:1');
    await fixture.whenStable();
    step.click();
    expect(emitted).toEqual(['nd_build:1', null]);
  });

  it('marks only the row matching selectedKey as selected', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    fixture.componentRef.setInput('selectedKey', 'nd_build:1');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="selection-step"]')?.classList.contains('selected')).toBe(true);
    expect(el.querySelector('[data-testid="selection-active"]')?.classList.contains('selected')).toBe(false);
  });

  it('is always activatable — a transition and the active row carry role=button with no activatable input to gate it', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="selection-step"]')?.getAttribute('role')).toBe('button');
    expect(el.querySelector('[data-testid="selection-active"]')?.getAttribute('role')).toBe('button');
  });

  it.each(['intent', 'follow-latest', null])('leaves %s migrations inert even with an origin node', async (source) => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', {
      ...TWO_GRAPH_DETAIL,
      migrations: [{ ...TWO_GRAPH_DETAIL.migrations![0], source }],
    });
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const migration = el.querySelector('[data-testid="selection-migration-step"]') as HTMLElement;
    expect(migration.getAttribute('role')).toBeNull();
    expect(migration.getAttribute('data-step-key')).toBeNull();
    migration.click();
    expect(emitted).toEqual([]);
  });

  it('activates the producing triage step by click, Enter and Space and marks it selected', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', {
      ...TWO_GRAPH_DETAIL,
      migrations: [{ ...TWO_GRAPH_DETAIL.migrations![0], source: 'authored-edge' }],
    });
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="selection-migration-step"]') as HTMLElement;
    expect(row.getAttribute('data-step-key')).toBe('nd_s_review:2');
    expect(row.getAttribute('role')).toBe('button');
    row.click();
    row.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    row.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
    expect(emitted).toEqual(['nd_s_review:2', 'nd_s_review:2', 'nd_s_review:2']);
    fixture.componentRef.setInput('selectedKey', 'nd_s_review:2');
    await fixture.whenStable();
    expect(row.classList.contains('selected')).toBe(true);
    row.click();
    expect(emitted.at(-1)).toBeNull();
  });

  it.each(['intent', 'follow-latest', 'authored-edge'])('keeps a null-origin %s migration unkeyed', async (source) => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', {
      ...TWO_GRAPH_DETAIL,
      migrations: [{ ...TWO_GRAPH_DETAIL.migrations![0], from_node_id: null, source }],
    });
    await fixture.whenStable();
    const row = (fixture.nativeElement as HTMLElement).querySelector('[data-testid="selection-migration-step"]') as HTMLElement;
    expect(row.getAttribute('role')).toBeNull();
    expect(row.getAttribute('data-step-key')).toBeNull();
  });

  it('renders no heading of its own', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', COST_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.textContent).not.toContain('Node history');
    expect(el.textContent).not.toContain('Timeline');
  });

  it('shows the empty state when there are no transitions and no active row', async () => {
    const fixture = TestBed.createComponent(ChunkTimelineSelection);
    fixture.componentRef.setInput('detail', { ...COST_DETAIL, status: 'not_ready', current_node_id: null, history: [] });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="selection-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="selection"]')).toBeNull();
  });
});
