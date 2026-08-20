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

async function render(options: { selectedKey?: string | null; stepArtifacts?: readonly ArtifactView[] } = {}) {
  // `ChunkTimelineSelection` renders a `RouterLink` on a multi-graph row — NG0201 without
  // a router provided, even though this chunk's single-graph history never reaches it.
  await TestBed.configureTestingModule({
    imports: [ChunkNodeHistoryTab],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkNodeHistoryTab);
  fixture.componentRef.setInput('detail', DETAIL);
  fixture.componentRef.setInput('selectedKey', options.selectedKey ?? null);
  fixture.componentRef.setInput('stepArtifacts', options.stepArtifacts ?? []);
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

  it('states the artifact empty case directly when a step is selected but has no artifacts', async () => {
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

  it('renders no transcript region — the runner cannot read transcripts through this tab', async () => {
    const fixture = await render({ selectedKey: 'nd_build:1', stepArtifacts: [STEP_ARTIFACT] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid^="node-history-transcript"]')).toBeNull();
  });
});
