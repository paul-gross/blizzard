import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { hubApi, WorkItemsState } from 'fleet';

import { ChunkGeneralTab } from './chunk-general-tab';

const DETAIL: hubApi.ChunkDetail = {
  chunk_id: 'ch_01general0000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 1,
  work_refs: [{ source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42' }],
  history: [],
  artifacts: [],
};

const WORK_ITEMS: WorkItemsState = {
  status: 'success',
  items: [
    { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42', fetched_at: 't', body: 'reproduces under load', comments: [] },
  ],
};

describe('ChunkGeneralTab', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkGeneralTab],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders every section off plain inputs, in attention order, with no artifacts/transcript section', async () => {
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', DETAIL);
    fixture.componentRef.setInput('workItems', WORK_ITEMS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const sections = Array.from(el.querySelectorAll('[data-testid^="section-"]')).map((node) =>
      node.getAttribute('data-testid'),
    );
    expect(sections).toEqual(['section-work-item', 'section-issues', 'section-node-history', 'section-asks']);
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
  });

  it('defaults workItems to a loading state when the caller supplies none', async () => {
    const fixture = TestBed.createComponent(ChunkGeneralTab);
    fixture.componentRef.setInput('detail', DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('fleet-chunk-detail-issue-pane')).not.toBeNull();
  });
});
