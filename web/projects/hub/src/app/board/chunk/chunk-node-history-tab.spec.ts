import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { ArtifactView, hubApi } from 'fleet';

import { ChunkNodeHistoryTab } from './chunk-node-history-tab';

const DETAIL: hubApi.ChunkDetail = {
  chunk_id: 'ch_01hover0000000000000000000',
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 2,
  status: 'running',
  work_refs: [],
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
  artifacts: [],
};

const STEP_ARTIFACT: ArtifactView = {
  key: 'build.plan.1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 1,
  name: 'plan',
  kind: 'asset',
  content: 'the plan',
  recorded_at: '2026-08-09T00:00:00.000Z',
};

interface RenderOptions {
  selectedKey?: string | null;
  stepArtifacts?: readonly ArtifactView[];
  indexState?: 'loading' | 'error' | 'empty' | 'ready';
  isForbidden?: boolean;
  segmentState?: 'loading' | 'error' | 'empty' | 'ready';
  segmentData?: { final: boolean; segment_id: string; truncated: boolean; turns?: unknown[] };
  continuedFrom?: { segment_id: string; spawn_generation: number } | null;
  continuesIn?: { segment_id: string; spawn_generation: number } | null;
}

async function render(options: RenderOptions = {}) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [ChunkNodeHistoryTab],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkNodeHistoryTab);
  fixture.componentRef.setInput('detail', DETAIL);
  fixture.componentRef.setInput('selectedKey', options.selectedKey ?? null);
  fixture.componentRef.setInput('stepArtifacts', options.stepArtifacts ?? []);
  fixture.componentRef.setInput('indexState', options.indexState ?? 'ready');
  fixture.componentRef.setInput('isForbidden', options.isForbidden ?? false);
  fixture.componentRef.setInput('segmentState', options.segmentState ?? 'empty');
  fixture.componentRef.setInput('segmentData', options.segmentData);
  fixture.componentRef.setInput('continuedFrom', options.continuedFrom ?? null);
  fixture.componentRef.setInput('continuesIn', options.continuesIn ?? null);
  await fixture.whenStable();
  return fixture;
}

describe('ChunkNodeHistoryTab', () => {
  it('renders the selection timeline with its own visible/labelled region', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const row = el.querySelector('[data-testid="selection-step"]');
    expect(row?.getAttribute('role')).toBe('button');

    const region = el.querySelector('[role="region"]');
    expect(region?.textContent).toContain('Timeline');
    const labelId = region?.getAttribute('aria-labelledby');
    expect(labelId).toBeTruthy();
    expect(el.querySelector(`#${labelId}`)).not.toBeNull();
  });

  it('marks the row matching selectedKey as selected', async () => {
    const fixture = await render({ selectedKey: 'nd_build:1' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="selection-step"]')?.classList.contains('selected')).toBe(true);
  });

  it("forwards the timeline's own pickStep straight through, unchanged", async () => {
    const fixture = await render();
    const emitted: (string | null)[] = [];
    fixture.componentInstance.pickStep.subscribe((key) => emitted.push(key));
    const el = fixture.nativeElement as HTMLElement;

    (el.querySelector('[data-testid="selection-step"]') as HTMLElement).click();
    expect(emitted).toEqual(['nd_build:1']);
  });

  it('shows a hint and no step panel when no step is selected', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="node-history-select-hint"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="node-history-artifacts-empty"]')).toBeNull();
  });

  it('renders both accordion sections open by default once a step is selected', async () => {
    const fixture = await render({ selectedKey: 'nd_build:1' });
    const el = fixture.nativeElement as HTMLElement;

    const heads = el.querySelectorAll('[data-testid="accordion-section-head"]');
    expect(heads).toHaveLength(2);
    for (const head of heads) {
      expect(head.getAttribute('aria-expanded')).toBe('true');
    }
    // Transcripts before artifacts.
    expect(heads[0].textContent).toContain('Transcript');
    expect(heads[1].textContent).toContain('Artifacts');
    expect(el.querySelectorAll('[data-testid="accordion-section-body"]')).toHaveLength(2);
  });

  it('collapses and re-expands a section independently of the other', async () => {
    const fixture = await render({ selectedKey: 'nd_build:1', stepArtifacts: [STEP_ARTIFACT] });
    const el = fixture.nativeElement as HTMLElement;
    const [transcriptHead, artifactsHead] = Array.from(
      el.querySelectorAll<HTMLButtonElement>('[data-testid="accordion-section-head"]'),
    );

    transcriptHead.click();
    await fixture.whenStable();

    expect(transcriptHead.getAttribute('aria-expanded')).toBe('false');
    expect(artifactsHead.getAttribute('aria-expanded')).toBe('true');
    expect(el.querySelector('[data-testid="node-history-artifact-key"]')).not.toBeNull();
  });

  it('states the artifact empty case directly, not gated on any query state (D7)', async () => {
    const fixture = await render({ selectedKey: 'nd_build:1', stepArtifacts: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="node-history-artifacts-empty"]')?.textContent).toContain(
      'No artifacts for this step.',
    );
  });

  it("renders the selected step's own artifacts via ChunkArtifactBody", async () => {
    const fixture = await render({ selectedKey: 'nd_build:1', stepArtifacts: [STEP_ARTIFACT] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="node-history-artifact-key"]')?.textContent).toContain('build.plan.1');
    expect(el.querySelector('[data-testid="node-history-artifact-content"]')?.textContent).toContain('the plan');
  });

  it('gates the transcript panel on indexState/segmentState — loading, forbidden, error, and empty', async () => {
    let fixture = await render({ selectedKey: 'nd_build:1', indexState: 'loading' });
    let el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="node-history-transcript-loading"]')).not.toBeNull();

    fixture = await render({ selectedKey: 'nd_build:1', isForbidden: true });
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="node-history-transcript-forbidden"]')).not.toBeNull();

    fixture = await render({ selectedKey: 'nd_build:1', indexState: 'error' });
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="node-history-transcript-error"]')).not.toBeNull();

    fixture = await render({ selectedKey: 'nd_build:1', segmentState: 'empty' });
    el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="node-history-transcript-empty"]')?.textContent).toContain(
      'NO TRANSCRIPT FOR THIS STEP',
    );
  });

  it("renders the selected step's own transcript once the segment resolves", async () => {
    const fixture = await render({
      selectedKey: 'nd_build:1',
      segmentState: 'ready',
      segmentData: {
        final: true,
        segment_id: 'sg_1',
        truncated: false,
        turns: [{ index: 0, kind: 'asst', text: 'hello from build', timestamp: null, tool: null, thinking_redacted: false, sidechain: null, truncated: false }],
      },
    });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="node-history-transcript-body"]')?.textContent).toContain(
      'hello from build',
    );
  });

  it('renders continued-from/continues-in seams and re-emits a followed seam as pickSegment (no dead-end pointer)', async () => {
    const fixture = await render({
      selectedKey: 'nd_build:1',
      segmentState: 'ready',
      segmentData: { final: true, segment_id: 'sg_2', truncated: false, turns: [] },
      continuedFrom: { segment_id: 'sg_1', spawn_generation: 0 },
      continuesIn: { segment_id: 'sg_3', spawn_generation: 2 },
    });
    const el = fixture.nativeElement as HTMLElement;
    const emitted: string[] = [];
    fixture.componentInstance.pickSegment.subscribe((id) => emitted.push(id));

    expect(el.querySelector('[data-testid="node-history-transcript-more"]')).toBeNull();

    const back = el.querySelector<HTMLButtonElement>('[data-testid="transcript-continued-from"]');
    expect(back?.textContent).toContain('segment 1');
    back?.click();

    const forward = el.querySelector<HTMLButtonElement>('[data-testid="transcript-continues-in"]');
    expect(forward?.textContent).toContain('segment 3');
    forward?.click();

    expect(emitted).toEqual(['sg_1', 'sg_3']);
  });
});
