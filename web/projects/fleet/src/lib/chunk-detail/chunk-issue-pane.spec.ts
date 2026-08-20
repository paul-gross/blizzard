import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ChunkIssuePane } from './chunk-issue-pane';
import type { WorkItemsState } from './work-items-state';

describe('ChunkIssuePane', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkIssuePane],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function renderWithWorkItems(workItems: WorkItemsState) {
    const fixture = TestBed.createComponent(ChunkIssuePane);
    fixture.componentRef.setInput('workItems', workItems);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows a loading notice while the forge read is in flight', async () => {
    const el = await renderWithWorkItems({ status: 'loading', items: [] });
    expect(el.querySelector('[data-testid="issue-loading"]')).not.toBeNull();
  });

  it('forwards the resolved items to the shared issue list (AC2, AC4)', async () => {
    const el = await renderWithWorkItems({
      status: 'success',
      items: [
        {
          source: 'widget',
          ref: '42',
          label: 'widget#42',
          web_url: 'https://github.com/acme/widget/issues/42',
          fetched_at: '2026-07-15T00:00:00Z',
          title: 'The widget flake',
          body: 'the widget flake reproduces under load',
          comments: ['seen it too', 'repro attached'],
          error: null,
        },
        {
          source: 'widget',
          ref: '43',
          label: 'widget#43',
          web_url: 'https://github.com/acme/widget/issues/43',
          fetched_at: '2026-07-15T00:00:00Z',
          title: 'A second ticket',
          body: 'second',
          comments: [],
          error: null,
        },
      ],
    });
    expect(el.querySelector('[data-testid="issue-pane"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="chunk-issue-list"]')).not.toBeNull();
    const rows = [...el.querySelectorAll('[data-testid="issue-name"]')].map((r) => r.textContent?.trim());
    expect(rows).toEqual(['The widget flake', 'A second ticket']);
  });

  it('shows an empty state when the chunk has no linked issue (AC4)', async () => {
    const el = await renderWithWorkItems({ status: 'success', items: [] });
    expect(el.querySelector('[data-testid="issue-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="chunk-issue-list"]')).toBeNull();
  });

  it('shows a visible notice when the whole forge read fails (AC5)', async () => {
    const el = await renderWithWorkItems({ status: 'error', items: [] });
    expect(el.querySelector('[data-testid="issue-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="chunk-issue-list"]')).toBeNull();
  });
});
