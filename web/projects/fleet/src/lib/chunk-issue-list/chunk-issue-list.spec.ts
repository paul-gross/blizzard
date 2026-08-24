import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { WorkItemAuthorView, WorkItemEntry } from '../api/hub';
import { ChunkIssueList } from './chunk-issue-list';

describe('ChunkIssueList', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkIssueList],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  function item(overrides: Partial<WorkItemEntry> & Pick<WorkItemEntry, 'source' | 'ref'>): WorkItemEntry {
    return {
      label: `${overrides.source}#${overrides.ref}`,
      web_url: `https://forge.example/${overrides.source}/issues/${overrides.ref}`,
      fetched_at: '2026-08-20T00:00:00Z',
      title: 'A ticket',
      body: 'the body',
      comments: [],
      error: null,
      author: null,
      stated_priority: null,
      ...overrides,
    };
  }

  function hubItem(overrides: Partial<WorkItemEntry> & { ref: string; author: WorkItemAuthorView }): WorkItemEntry {
    return item({
      source: 'hub',
      web_url: `/board/chunk/ch_${overrides.ref}`,
      body: '# heading\n\nplain body',
      ...overrides,
    });
  }

  const USER_AUTHOR: WorkItemAuthorView = {
    kind: 'user',
    user_id: 'usr_1',
    login: 'alice',
    runner_id: null,
    chunk_id: null,
    node_name: null,
  };

  const FLEET_AUTHOR: WorkItemAuthorView = {
    kind: 'fleet',
    user_id: null,
    login: null,
    runner_id: 'runner-local',
    chunk_id: 'ch_proposer',
    node_name: 'triage',
  };

  async function render(items: readonly WorkItemEntry[]) {
    const fixture = TestBed.createComponent(ChunkIssueList);
    fixture.componentRef.setInput('items', items);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('renders the ticket name before the ref, the ref as a dash-prefixed link', async () => {
    const { el } = await render([item({ source: 'widget', ref: '42', title: 'The widget flake' })]);
    const row = el.querySelector('[data-testid="issue-row"]') as HTMLElement;
    expect(row.querySelector('[data-testid="issue-name"]')?.textContent).toBe('The widget flake');
    const link = el.querySelector<HTMLAnchorElement>('[data-testid="issue-ref"]');
    expect(link?.textContent?.trim()).toBe('- widget#42');
    expect(link?.getAttribute('href')).toBe('https://forge.example/widget/issues/42');
  });

  it('renders the ref link beside the accordion trigger, not nested inside its button', async () => {
    const { el } = await render([item({ source: 'widget', ref: '42' })]);
    const link = el.querySelector<HTMLAnchorElement>('[data-testid="issue-ref"]');
    expect(link?.closest('button')).toBeNull();
  });

  it('starts expanded when there is exactly one issue', async () => {
    const { el } = await render([item({ source: 'widget', ref: '42' })]);
    expect(el.querySelector('[data-testid="accordion-section-head"]')?.getAttribute('aria-expanded')).toBe('true');
    expect(el.querySelector('[data-testid="issue-body"]')).not.toBeNull();
  });

  it('starts every section collapsed when there is more than one issue', async () => {
    const { el } = await render([item({ source: 'widget', ref: '42' }), item({ source: 'widget', ref: '43' })]);
    const heads = el.querySelectorAll('[data-testid="accordion-section-head"]');
    expect(heads).toHaveLength(2);
    for (const head of Array.from(heads)) {
      expect(head.getAttribute('aria-expanded')).toBe('false');
    }
    expect(el.querySelector('[data-testid="issue-body"]')).toBeNull();
  });

  it('toggles one section without disturbing the others, and more than one can read open at once', async () => {
    const { fixture, el } = await render([
      item({ source: 'widget', ref: '42', title: 'First' }),
      item({ source: 'widget', ref: '43', title: 'Second' }),
    ]);
    const heads = el.querySelectorAll<HTMLButtonElement>('[data-testid="accordion-section-head"]');
    heads[0].click();
    await fixture.whenStable();
    heads[1].click();
    await fixture.whenStable();

    const headsAfter = el.querySelectorAll('[data-testid="accordion-section-head"]');
    expect(headsAfter[0].getAttribute('aria-expanded')).toBe('true');
    expect(headsAfter[1].getAttribute('aria-expanded')).toBe('true');
    expect(el.querySelectorAll('[data-testid="issue-body"]')).toHaveLength(2);
  });

  it('keeps the ref link independently clickable without toggling the section', async () => {
    const { fixture, el } = await render([
      item({ source: 'widget', ref: '42' }),
      item({ source: 'widget', ref: '43' }),
    ]);
    const link = el.querySelector<HTMLAnchorElement>('[data-testid="issue-ref"]');
    // jsdom-style anchor navigation throws unless prevented; the assertion that
    // matters here is the one below — the click never reached the accordion's
    // own toggle handler.
    link?.addEventListener('click', (e) => e.preventDefault());
    link?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="accordion-section-head"]')?.getAttribute('aria-expanded')).toBe('false');
  });

  it('renders a sane header when the title is missing, rather than a dangling ref (toy-api-style forge failure)', async () => {
    const { el } = await render([
      item({
        source: 'toyapi',
        ref: '1',
        title: null,
        body: null,
        error: "failed to read toyapi#1: Client error '404 Not Found'",
      }),
    ]);
    expect(el.querySelector('[data-testid="issue-name"]')?.textContent).toBe('—');
    expect(el.querySelector('[data-testid="issue-ref"]')?.textContent?.trim()).toBe('- toyapi#1');
    // A single issue starts expanded, so the per-item error notice is visible.
    expect(el.querySelector('[data-testid="issue-item-error"]')?.textContent).toContain('404 Not Found');
    expect(el.querySelector('[data-testid="issue-body"]')).toBeNull();
  });

  it('shows the no-messages notice for an issue with none, once expanded', async () => {
    const { el } = await render([item({ source: 'widget', ref: '42', comments: [] })]);
    expect(el.querySelector('[data-testid="issue-no-messages"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="issue-messages"]')).toBeNull();
  });

  it('lists every message for an issue that has them', async () => {
    const { el } = await render([item({ source: 'widget', ref: '42', comments: ['seen it too', 'repro attached'] })]);
    const messages = [...el.querySelectorAll('[data-testid="issue-message"]')].map((m) => m.textContent?.trim());
    expect(messages).toEqual(['seen it too', 'repro attached']);
  });

  // --- The hub idiom (blizzard#362) ------------------------------------------

  it('a hub entry renders its markdown body, a user authorship line, stated priority, and no forge anchor', async () => {
    const { el } = await render([hubItem({ ref: '1', author: USER_AUTHOR, stated_priority: 'high' })]);

    expect(el.querySelector('[data-testid="issue-body"] h1')?.textContent?.trim()).toBe('heading');
    expect(el.querySelector('[data-testid="issue-author"]')?.textContent).toContain('alice');
    expect(el.querySelector('[data-testid="issue-priority"]')?.textContent?.trim()).toBe('high');
    const ref = el.querySelector<HTMLAnchorElement>('[data-testid="issue-ref"]');
    expect(ref?.getAttribute('target')).toBeNull();
    expect(ref?.getAttribute('rel')).toBeNull();
  });

  it('a fleet-authored entry names the runner, chunk, and node, the chunk a routerLink through linkBase()', async () => {
    const { el } = await render([hubItem({ ref: '2', author: FLEET_AUTHOR })]);

    const author = el.querySelector('[data-testid="issue-author"]');
    expect(author?.textContent).toContain('runner-local');
    expect(author?.textContent).toContain('triage');
    const chunkLink = el.querySelector<HTMLAnchorElement>('[data-testid="issue-author-chunk"]');
    expect(chunkLink?.textContent?.trim()).toBe('ch_proposer');
    expect(chunkLink?.getAttribute('href')).toBe('/board/chunk/ch_proposer');
  });

  it('a forge entry renders unchanged — anchor included, no author line, no priority badge', async () => {
    const { el } = await render([item({ source: 'widget', ref: '42' })]);

    expect(el.querySelector('[data-testid="issue-ref"]')?.getAttribute('target')).toBe('_blank');
    expect(el.querySelector('[data-testid="issue-body"] h1')).toBeNull();
    expect(el.querySelector('[data-testid="issue-author"]')).toBeNull();
    expect(el.querySelector('[data-testid="issue-priority"]')).toBeNull();
  });

  it('a mixed chunk renders both idioms in pointer order, one entry error suppressing neither the other nor the pane', async () => {
    const { fixture, el } = await render([
      hubItem({ ref: '3', author: USER_AUTHOR, title: 'hub-authored', error: 'no open hub:3 work item exists' }),
      item({ source: 'widget', ref: '42', title: 'forge-tracked' }),
    ]);

    const names = [...el.querySelectorAll('[data-testid="issue-name"]')].map((n) => n.textContent);
    expect(names).toEqual(['hub-authored', 'forge-tracked']);
    // Two entries: each starts collapsed, so expand both to check their bodies.
    const heads = el.querySelectorAll<HTMLButtonElement>('[data-testid="accordion-section-head"]');
    heads[0].click();
    heads[1].click();
    await fixture.whenStable();
    const errors = el.querySelectorAll('[data-testid="issue-item-error"]');
    expect(errors).toHaveLength(1);
    expect(errors[0].textContent).toContain('no open hub:3 work item exists');
    expect(el.querySelector('[data-testid="issue-body"]')?.textContent).toContain('the body');
  });
});
