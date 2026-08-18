import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { hubApi } from 'fleet';

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

async function render(selectedKey: string | null = null) {
  await TestBed.configureTestingModule({
    imports: [ChunkNodeHistoryTab],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkNodeHistoryTab);
  fixture.componentRef.setInput('detail', DETAIL);
  fixture.componentRef.setInput('selectedKey', selectedKey);
  await fixture.whenStable();
  return fixture;
}

describe('ChunkNodeHistoryTab', () => {
  it('renders the timeline with no heading of its own and row activation on', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('#chunk-timeline-heading')).toBeNull();
    const row = el.querySelector('[data-testid="history-step"]');
    expect(row?.getAttribute('role')).toBe('button');
  });

  it('marks the row matching selectedKey as selected', async () => {
    const fixture = await render('nd_build:1');
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="history-step"]')?.classList.contains('selected')).toBe(true);
  });

  it("forwards the timeline's own selectStep straight through, unchanged", async () => {
    const fixture = await render();
    const emitted: string[] = [];
    fixture.componentInstance.selectStep.subscribe((key) => emitted.push(key));
    const el = fixture.nativeElement as HTMLElement;

    (el.querySelector('[data-testid="history-step"]') as HTMLElement).click();
    expect(emitted).toEqual(['nd_build:1']);
  });
});
