import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { KitAsyncStateValue } from 'fleet';

import type { GlanceListGroups, GlanceRow } from './glance-board.model';
import { GlanceView, type Vitals } from './glance-view';

const VITALS: Vitals = {
  needsYou: 0,
  running: 0,
  ready: 0,
  doneToday: 0,
  runnersUpLabel: '0/0',
};

const EMPTY: GlanceListGroups = { live: [], ready: [], backlog: [], done: [] };

/** A running card with nothing spent, no runner, and no work items, unless a case says otherwise. */
const row = (fields: Partial<GlanceRow> = {}): GlanceRow => ({
  chunkId: 'ch_01row0000000000000000000000',
  shortId: 'C-0001',
  lane: 'running',
  status: 'running',
  statusWord: null,
  node: 'build',
  runner: null,
  gated: false,
  ask: null,
  lines: [],
  itemsRead: true,
  costUsd: 0,
  costPartial: false,
  estimatedCostUsd: null,
  projectColor: null,
  ...fields,
});

const text = (el: Element | null | undefined): string | undefined => el?.textContent?.replace(/\s+/g, ' ').trim();

describe('GlanceView', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [GlanceView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  function render(list: Partial<GlanceListGroups> = {}, listState: KitAsyncStateValue = 'ready', spendState: KitAsyncStateValue = 'ready') {
    const fixture = TestBed.createComponent(GlanceView);
    fixture.componentRef.setInput('vitals', VITALS);
    fixture.componentRef.setInput('list', { ...EMPTY, ...list });
    fixture.componentRef.setInput('listState', listState);
    fixture.componentRef.setInput('spendState', spendState);
    return fixture;
  }

  async function cards(list: Partial<GlanceListGroups>): Promise<HTMLElement[]> {
    const fixture = render(list);
    await fixture.whenStable();
    return [...(fixture.nativeElement as HTMLElement).querySelectorAll<HTMLElement>('[data-testid="glance-card"]')];
  }

  it('renders the five vitals', async () => {
    const fixture = render();
    fixture.componentRef.setInput('vitals', { needsYou: 2, running: 3, ready: 4, doneToday: 5, runnersUpLabel: '1/2' });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const vital = (id: string) => text(el.querySelector(`[data-testid="${id}"] .v-n`));
    expect([vital('vital-needs-you'), vital('vital-running'), vital('vital-ready'), vital('vital-done-today'), vital('vital-runners-up')]).toEqual([
      '2',
      '3',
      '4',
      '5',
      '1/2',
    ]);
  });

  it('shows the list\'s loading, error, and empty states in place of the list', async () => {
    const fixture = render({}, 'loading');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="glance-list-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="glance-list"]')).toBeNull();

    fixture.componentRef.setInput('listState', 'error');
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="glance-list-error"]')).not.toBeNull();

    fixture.componentRef.setInput('listState', 'empty');
    await fixture.whenStable();
    expect(text(el.querySelector('[data-testid="glance-list-empty"]'))).toContain('NOTHING ON THE BOARD');
  });

  it('shows the spend panel\'s own loading placeholder distinctly from its error state', async () => {
    const fixture = render({}, 'ready', 'loading');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="glance-spend-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="glance-spend-row"]')).toBeNull();

    fixture.componentRef.setInput('spendState', 'error');
    await fixture.whenStable();
    expect(el.querySelector('[data-testid="glance-spend-error"]')).not.toBeNull();
  });

  it('renders live, then READY, the divider, BACKLOG, then done, as one list', async () => {
    const fixture = render({
      live: [row({ chunkId: 'ch_live' })],
      ready: [row({ chunkId: 'ch_ready', lane: 'ready', status: 'ready' })],
      backlog: [row({ chunkId: 'ch_backlog', lane: 'notready', status: 'not_ready' })],
      done: [row({ chunkId: 'ch_done', lane: 'done', status: 'done' })],
    });
    await fixture.whenStable();
    const items = [...(fixture.nativeElement as HTMLElement).querySelectorAll('[data-testid="glance-list"] > li')];

    expect(items.map((li) => li.getAttribute('data-chunk') ?? li.getAttribute('data-testid'))).toEqual([
      'ch_live',
      'ch_ready',
      'glance-ready-divider',
      'ch_backlog',
      'ch_done',
    ]);
  });

  it('keeps the divider with nothing either side of it', async () => {
    const fixture = render({ live: [row()] });
    await fixture.whenStable();

    expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="glance-ready-divider"]')).not.toBeNull();
  });

  it('meta-lines a card with its node and holding runner, host and name each their own span', async () => {
    const [card] = await cards({ live: [row({ node: 'review', runner: { host: 'atlas', name: 'r-claude' } })] });

    expect(text(card.querySelector('.step'))).toBe('review');
    expect(text(card.querySelector('[data-testid="glance-runner"] .r-host'))).toBe('atlas');
    expect(text(card.querySelector('[data-testid="glance-runner"] .r-name'))).toBe('r-claude');
    expect(text(card.querySelector('[data-testid="glance-runner"]'))).toBe('atlas/r-claude');
  });

  it('names a runner with no known host by its name alone, and no runner at all where none holds it', async () => {
    const [hostless, idle] = await cards({
      live: [row({ chunkId: 'ch_a', runner: { host: null, name: 'r-claude' } }), row({ chunkId: 'ch_b' })],
    });

    expect(hostless.querySelector('.r-host')).toBeNull();
    expect(text(hostless.querySelector('[data-testid="glance-runner"]'))).toBe('r-claude');
    expect(idle.querySelector('[data-testid="glance-runner"]')).toBeNull();
  });

  it('pills an open gate, words a paused status, and carries an open ask under the card', async () => {
    const [card] = await cards({ live: [row({ gated: true, statusWord: 'paused', ask: 'Which branch?' })] });

    expect(text(card.querySelector('.gate'))).toBe('gate');
    expect(text(card.querySelector('[data-testid="glance-row-status"]'))).toBe('paused');
    expect(text(card.querySelector('[data-testid="glance-ask"]'))).toBe('Which branch?');
  });

  it('carries no gate, status word, or ask where the row has none', async () => {
    const [card] = await cards({ live: [row()] });

    expect(card.querySelector('.gate')).toBeNull();
    expect(card.querySelector('[data-testid="glance-row-status"]')).toBeNull();
    expect(card.querySelector('[data-testid="glance-ask"]')).toBeNull();
  });

  it('tints a card with its project color and edges it with its status color', async () => {
    const [tinted, plain] = await cards({ live: [row({ chunkId: 'ch_a', projectColor: 'teal' }), row({ chunkId: 'ch_b' })] });

    expect(tinted.classList).toContain('tinted');
    expect(tinted.style.getPropertyValue('--project-tint')).not.toBe('');
    expect(tinted.style.getPropertyValue('--status-color')).not.toBe('');
    expect(plain.classList).not.toContain('tinted');
  });

  it('links each card to its chunk\'s detail page', async () => {
    const [card] = await cards({ live: [row({ chunkId: 'ch_01link', shortId: 'C-LINK' })] });

    const hit = card.querySelector('a.hit');
    expect(hit?.getAttribute('href')).toContain('/board/chunk/ch_01link');
    expect(hit?.getAttribute('aria-label')).toBe('Open chunk C-LINK');
  });

  it("renders a card's and the spend panel's own entirely-estimated figure as the estimate alone, marked ~", async () => {
    const fixture = render({ live: [row({ estimatedCostUsd: 0.07 })] });
    fixture.componentRef.setInput('spend', {
      since: '2026-07-20T00:00:00Z',
      input_tokens: 900,
      output_tokens: 400,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      cost_usd: 0,
      cost_partial: false,
      estimated_cost_usd: 0.07,
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(text(el.querySelector('[data-testid="glance-cost"]'))).toBe('~$0.07');
    expect(text(el.querySelector('[data-testid="glance-spend-value"]'))).toBe('~$0.07');
  });

  it('folds a billed cost and its own estimate into one card figure, marked ~', async () => {
    const [card] = await cards({ live: [row({ costUsd: 0.05, estimatedCostUsd: 2 })] });

    expect(text(card.querySelector('[data-testid="glance-cost"]'))).toBe('~$2.05');
  });

  it('renders no ~ marker for a chunk/spend read with no estimate', async () => {
    const fixture = render({ live: [row({ costUsd: 2.03 })] });
    fixture.componentRef.setInput('spend', {
      since: '2026-07-20T00:00:00Z',
      input_tokens: 900,
      output_tokens: 400,
      cache_read_tokens: 0,
      cache_create_tokens: 0,
      cost_usd: 18.4,
      cost_partial: false,
    });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(text(el.querySelector('[data-testid="glance-cost"]'))).toBe('$2.03');
    expect(text(el.querySelector('[data-testid="glance-spend-row"]'))).toContain('$18.40');
  });

  it('renders no cost figure on a card that has spent nothing and carries no estimate', async () => {
    const [card] = await cards({ live: [row()] });

    expect(card.querySelector('[data-testid="glance-cost"]')).toBeNull();
  });

  it("leads a card with its work item's label and title", async () => {
    const [card] = await cards({ live: [row({ lines: [{ label: 'toy-web#6', title: 'Announce form errors', url: null }] })] });

    expect(text(card.querySelector('[data-testid="glance-work-ref"]'))).toBe('toy-web#6');
    expect(text(card.querySelector('[data-testid="glance-title"]'))).toBe('Announce form errors');
  });

  it("holds a card's title in a skeleton until its work items are read", async () => {
    const [card] = await cards({ ready: [row({ lines: [{ label: 'toy-api#1', title: null, url: null }], itemsRead: false })] });

    expect(text(card.querySelector('[data-testid="glance-work-ref"]'))).toBe('toy-api#1');
    expect(card.querySelector('[data-testid="glance-title-skeleton"]')).not.toBeNull();
    expect(card.querySelector('[data-testid="glance-title"]')).toBeNull();
  });

  it('heads a card with no work item with its short id alone, and no empty work-ref', async () => {
    const [card] = await cards({ ready: [row({ shortId: 'C-0007' })] });

    expect(text(card.querySelector('.head'))).toBe('C-0007');
    expect(card.querySelector('[data-testid="glance-work-ref"]')).toBeNull();
  });

  it("names a grouped chunk's later work items each under its own label", async () => {
    const [card] = await cards({
      ready: [
        row({
          lines: [
            { label: 'toy-api#1', title: 'Rate-limit login', url: null },
            { label: 'toy-web#3', title: 'Drop moment.js', url: null },
          ],
        }),
      ],
    });

    expect([...card.querySelectorAll('[data-testid="glance-work-ref"]')].map(text)).toEqual(['toy-api#1', 'toy-web#3']);
    expect([...card.querySelectorAll('[data-testid="glance-title"]')].map(text)).toEqual(['Rate-limit login', 'Drop moment.js']);
  });
});
