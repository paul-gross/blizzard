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
  pm_pointers: [],
  history: [],
  artifacts: [],
  route: { runner_id: 'rn_01', workspace_id: 'ws_01', environment_ids: ['env_01'] },
};

// A not_ready chunk — the one window issue #27's graph/model edit is open.
const NOT_READY_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01ready000000000000000000000',
  graph_id: 'gr_default',
  model: 'claude-opus-4-8',
  status: 'not_ready',
  current_node_id: null,
  latest_epoch: null,
  pm_pointers: [],
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

  it('shows the chunk’s current graph and model as plain facts for a running chunk', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', { ...ROUTED_DETAIL, model: 'claude-sonnet-4-5' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-value"]')?.textContent?.trim()).toBe('gr_1');
    expect(el.querySelector('[data-testid="model-value"]')?.textContent?.trim()).toBe('claude-sonnet-4-5');
  });

  it('offers the graph and model edit inputs for a not_ready chunk', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', NOT_READY_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="graph-input"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="graph-submit"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="model-input"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="model-submit"]')).not.toBeNull();
  });

  it('withholds the graph and model edit inputs once the chunk has left not_ready', async () => {
    for (const status of ['ready', 'running', 'delivering', 'waiting_on_human', 'needs_human', 'paused', 'stopped', 'done'] as const) {
      const fixture = TestBed.createComponent(ChunkFacts);
      fixture.componentRef.setInput('detail', { ...NOT_READY_DETAIL, status });
      await fixture.whenStable();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="graph-input"]'), status).toBeNull();
      expect(el.querySelector('[data-testid="model-input"]'), status).toBeNull();
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

  it('emits editModel with the typed model when Set is activated', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', NOT_READY_DETAIL);
    let emitted: { chunkId: string; model: string } | undefined;
    fixture.componentInstance.editModel.subscribe((event) => (emitted = event));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const input = el.querySelector<HTMLInputElement>('[data-testid="model-input"]')!;
    input.value = 'claude-sonnet-4-5';
    el.querySelector<HTMLButtonElement>('[data-testid="model-submit"]')?.click();

    expect(emitted).toEqual({ chunkId: NOT_READY_DETAIL.chunk_id, model: 'claude-sonnet-4-5' });
  });

  it('does not emit editModel for a blank model', async () => {
    const fixture = TestBed.createComponent(ChunkFacts);
    fixture.componentRef.setInput('detail', NOT_READY_DETAIL);
    let emitted = false;
    fixture.componentInstance.editModel.subscribe(() => (emitted = true));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="model-submit"]')?.click();
    expect(emitted).toBe(false);
  });
});
