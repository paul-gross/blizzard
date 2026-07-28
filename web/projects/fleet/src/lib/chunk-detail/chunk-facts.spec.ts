import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ChunkDetail } from '../api/hub';
import { ChunkFacts } from './chunk-facts';

const ROUTED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01routed000000000000000000',
  graph_id: 'gr_1',
  model: 'claude-opus-4-8',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', workspace_id: 'ws_01', environment_ids: ['env_01'] },
};

// A not_ready chunk — the one window issue #27's graph edit is open.
const NOT_READY_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01ready000000000000000000000',
  graph_id: 'gr_default',
  model: 'claude-opus-4-8',
  status: 'not_ready',
  current_node_id: null,
  latest_epoch: null,
  work_refs: [],
  history: [],
  artifacts: [],
};

describe('ChunkFacts', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkFacts],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('states the chunk facts, naming the runner holding its route', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, current_node_name: 'build' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const fact = (key: string) => el.querySelector(`[data-testid="fact-${key}"]`)?.textContent?.trim();
    expect(fact('status')).toBe('running');
    expect(fact('node')).toBe('build');
    expect(fact('runner')).toBe('rn_01');
    expect(fact('attempts')).toBe('1');
  });

  it('reads attempts as em-dash, not 0, for a chunk no runner has ever worked', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, latest_epoch: null, route: null });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fact-attempts"]')?.textContent?.trim()).toBe('—');
    expect(el.querySelector('[data-testid="fact-runner"]')?.textContent?.trim()).toBe('—');
  });

  it('shows the chunk’s current graph as a compact ref for a running chunk', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', ROUTED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const graphValue = el.querySelector('[data-testid="graph-value"]');
    expect(graphValue?.textContent?.trim()).toBe('G-1');
    expect(graphValue?.getAttribute('title')).toBe('gr_1');
  });

  it('renders the graph as compactRef#name-YYYYMMDD when the graph name and creation date are present (issue #102)', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', {
      ...ROUTED_DETAIL,
      graph_id: 'gr_01KXV4DP7T7NKBE8GJJ1GXG857',
      graph_name: 'glacier',
      graph_created_at: '2026-07-19T10:00:00Z',
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const graphValue = el.querySelector('[data-testid="graph-value"]');
    expect(graphValue?.textContent?.trim()).toBe('G-G857#glacier-20260719');
    expect(graphValue?.getAttribute('title')).toBe('gr_01KXV4DP7T7NKBE8GJJ1GXG857');
  });

  it('degrades the graph label to the bare compact ref when the creation date is absent', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', {
      ...ROUTED_DETAIL,
      graph_id: 'gr_01KXV4DP7T7NKBE8GJJ1GXG857',
      graph_name: 'glacier',
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const graphValue = el.querySelector('[data-testid="graph-value"]');
    expect(graphValue?.textContent?.trim()).toBe('G-G857');
    expect(graphValue?.textContent).not.toContain('#');
  });

  it('degrades the graph label to the bare compact ref when the name is absent', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', {
      ...ROUTED_DETAIL,
      graph_id: 'gr_01KXV4DP7T7NKBE8GJJ1GXG857',
      graph_created_at: '2026-07-19T10:00:00Z',
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const graphValue = el.querySelector('[data-testid="graph-value"]');
    expect(graphValue?.textContent?.trim()).toBe('G-G857');
    expect(graphValue?.textContent).not.toContain('#');
  });

  it('degrades the graph label to the bare compact ref when the creation date is present but unparseable', async () => {
    // consider-5 from the #118 pre-push review: `graphLabel` guarded on field presence,
    // not parse success — a malformed `graph_created_at` used to fall through to the
    // dangling `G-G857#glacier-` the docstring rules out, since `formatUtcYmd` degrades
    // to `''` for an unparseable instant rather than throwing.
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', {
      ...ROUTED_DETAIL,
      graph_id: 'gr_01KXV4DP7T7NKBE8GJJ1GXG857',
      graph_name: 'glacier',
      graph_created_at: 'not-a-date',
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const graphValue = el.querySelector('[data-testid="graph-value"]');
    expect(graphValue?.textContent?.trim()).toBe('G-G857');
    expect(graphValue?.textContent).not.toContain('#');
  });

  it('offers the graph edit input for a not_ready chunk', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', NOT_READY_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-input"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="graph-submit"]')).not.toBeNull();
  });

  it('offers the graph edit input for a ready, unclaimed chunk (issue #120)', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', { ...NOT_READY_DETAIL, status: 'ready' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-input"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="graph-submit"]')).not.toBeNull();
  });

  it('renders no model row at all (issue #144)', async () => {
    // `Chunk.model` is retired and its replacements have no web editing surface, so the
    // row is gone rather than turned read-only — a read-only row for a field the board
    // cannot write and the fleet no longer runs on would be worse than absent.
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', NOT_READY_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="fact-model"]')).toBeNull();
    expect(el.querySelector('[data-testid="model-input"]')).toBeNull();
  });

  it('withholds the graph edit input once the chunk is claimed', async () => {
    for (const status of ['running', 'delivering', 'waiting_on_human', 'needs_human', 'paused', 'stopped', 'done'] as const) {
      const fixture = TestBed.createComponent(ChunkFacts);
      fixture.componentRef.setInput('detail', { ...NOT_READY_DETAIL, status });
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="graph-input"]'), status).toBeNull();
      expect(el.querySelector('[data-testid="graph-value"]'), status).not.toBeNull();
    }
  });

  it('emits editGraph with the typed graph id when Set is activated', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', NOT_READY_DETAIL);
    let emitted: { chunkId: string; graphId: string } | undefined;
    fixture.componentInstance.editGraph.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="graph-input"]')!;
    input.value = 'gr_alt';
    el.querySelector<HTMLButtonElement>('[data-testid="graph-submit"]')?.click();

    expect(emitted).toEqual({ chunkId: NOT_READY_DETAIL.chunk_id, graphId: 'gr_alt' });
  });

  it('does not emit editGraph for a blank graph id', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', NOT_READY_DETAIL);
    let emitted = false;
    fixture.componentInstance.editGraph.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="graph-submit"]')?.click();
    expect(emitted).toBe(false);
  });
});
