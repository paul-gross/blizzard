import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ChunkIssuePane, type PmItemsState } from './chunk-issue-pane';

describe('ChunkIssuePane', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkIssuePane],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  async function renderWithPmItems(pmItems: PmItemsState) {
    const fixture = TestBed.createComponent(ChunkIssuePane);
    fixture.componentRef.setInput('pmItems', pmItems);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('shows a loading notice while the forge read is in flight', async () => {
    const el = await renderWithPmItems({ status: 'loading', items: [] });
    expect(el.querySelector('[data-testid="issue-loading"]')).not.toBeNull();
  });

  it('renders the issue description and messages in the work-item column (AC2)', async () => {
    const el = await renderWithPmItems({
      status: 'success',
      items: [
        {
          source: 'widget',
          ref: '42',
          label: 'widget#42',
          web_url: 'https://github.com/acme/widget/issues/42',
          fetched_at: '2026-07-15T00:00:00Z',
          body: 'the widget flake reproduces under load',
          comments: ['seen it too', 'repro attached'],
          error: null,
        },
      ],
    });
    expect(el.querySelector('[data-testid="issue-pane"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-label"]')?.textContent).toContain('widget#42');
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reproduces under load');
    const messages = [...el.querySelectorAll('[data-testid="issue-message"]')].map((m) => m.textContent?.trim());
    expect(messages).toEqual(['seen it too', 'repro attached']);
    expect(el.querySelector<HTMLAnchorElement>('[data-testid="issue-label"]')?.getAttribute('href')).toBe(
      'https://github.com/acme/widget/issues/42',
    );
  });

  it('shows one entry per pointer for a grouped chunk (AC4)', async () => {
    const el = await renderWithPmItems({
      status: 'success',
      items: [
        { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42', fetched_at: 't', body: 'first', comments: [] },
        { source: 'widget', ref: '43', label: 'widget#43', web_url: 'https://github.com/acme/widget/issues/43', fetched_at: 't', body: 'second', comments: [] },
      ],
    });
    const items = el.querySelectorAll('[data-testid="issue-item"]');
    expect(items).toHaveLength(2);
    const bodies = [...el.querySelectorAll('[data-testid="issue-body"]')].map((b) => b.textContent?.trim());
    expect(bodies).toEqual(['first', 'second']);
  });

  it('shows an empty state when the chunk has no linked issue (AC4)', async () => {
    const el = await renderWithPmItems({ status: 'success', items: [] });
    expect(el.querySelector('[data-testid="issue-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-item"]')).toBeNull();
  });

  it('degrades a single unreachable pointer to an inline notice (AC5)', async () => {
    const el = await renderWithPmItems({
      status: 'success',
      items: [
        { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42', fetched_at: 't', body: 'reachable', comments: [] },
        { source: 'widget', ref: '43', label: 'widget#43', web_url: 'https://github.com/acme/widget/issues/43', fetched_at: 't', body: null, comments: [], error: 'forge unreachable for issues/43' },
      ],
    });
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('reachable');
    expect(el.querySelector('[data-testid="issue-item-error"]')?.textContent).toContain('forge unreachable');
  });

  it('shows a visible notice when the whole forge read fails (AC5)', async () => {
    const el = await renderWithPmItems({ status: 'error', items: [] });
    expect(el.querySelector('[data-testid="issue-error"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-body"]')).toBeNull();
  });

  it('shows a no-messages notice for an issue with none', async () => {
    const el = await renderWithPmItems({
      status: 'success',
      items: [
        { source: 'widget', ref: '42', label: 'widget#42', web_url: 'https://github.com/acme/widget/issues/42', fetched_at: 't', body: 'no comments here', comments: [] },
      ],
    });
    expect(el.querySelector('[data-testid="issue-no-messages"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-messages"]')).toBeNull();
  });
});
