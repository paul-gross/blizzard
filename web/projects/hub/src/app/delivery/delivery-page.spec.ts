import { provideLocationMocks } from '@angular/common/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { FLEET_CLOCK, hubClient } from 'fleet';
import {
  bounceView,
  FIXTURE_NOW,
  chunkDetail,
  chunkSummary,
  fixtureInstant,
  graphView,
  landedRepoView,
  prView,
  type RequestClientStub,
  settle,
  stubError,
  stubRequestClient,
} from 'fleet/testing';
import { DeliveryPage } from './delivery-page';

const IN_LINE = 'ch_01inline0000000000000000000';
const BOUNCED = 'ch_01bounced000000000000000000';
const LANDED = 'ch_01landed0000000000000000000';
const IDLE = 'ch_01idle000000000000000000000';

const PLACE = { flow_id: 'fl_a', position: 2, state: 'waiting', branch: 'master', next_poll_at: null, repository_name: 'blizzard', repository_slug: null, resource: 'rp_a:master' } as const;

const CHUNKS = [
  chunkSummary({ chunk_id: IN_LINE, graph_id: 'gr_1', status: 'running', current_node_id: 'nd_build', current_node_name: 'build', open_prs: [prView({ number: 41 })], flow_places: [PLACE] }),
  chunkSummary({ chunk_id: BOUNCED, graph_id: 'gr_1', status: 'needs_human' }),
  chunkSummary({ chunk_id: LANDED, graph_id: 'gr_1', status: 'done', terminal: true, landed: true, landed_repos: [landedRepoView()], completed_at: fixtureInstant(-3 * 3600) }),
  chunkSummary({ chunk_id: IDLE, graph_id: 'gr_1', status: 'ready' }),
];

const fact = (id: number, chunkId: string, kind: string, secondsAgo: number, outcome: string | null = null) => ({
  id,
  flow_id: 'fl_a',
  chunk_id: chunkId,
  kind,
  at: fixtureInstant(-secondsAgo),
  outcome,
  revision: null,
  submission_id: `sub-${chunkId}`,
});

/** The page's reads; everything unnamed falls through to `{}`. `flows` answers the active-flows read. */
function hubRoutes(flows: () => unknown = () => ({ flows: [{ flow_id: 'fl_a', repository_id: 'rp_a', line: [], resource: 'rp_a:master', branch: 'master' }] })) {
  return (method: string, path: string): unknown => {
    if (method !== 'GET') return {};
    if (path === '/api/flows') return flows();
    if (path === '/api/repositories') return { repositories: [{ repository_id: 'rp_a', owner: 'paul-gross', repo: 'blizzard', name: 'blizzard' }] };
    if (path === '/api/chunks') return { chunks: CHUNKS, next_cursor: null };
    if (path === '/api/flows/fl_a/record') {
      return { facts: [fact(3, BOUNCED, 'turn-ended', 600, 'failure'), fact(2, IN_LINE, 'submitted', 1800), fact(1, LANDED, 'landed', 7200)], next_cursor: null };
    }
    if (path === '/api/work-items') return { chunks: {} };
    if (path === '/api/graphs/gr_1') return graphView({ graph_id: 'gr_1', name: 'adv-dwf' });
    if (path === `/api/chunks/${IN_LINE}`) {
      return chunkDetail({ chunk_id: IN_LINE, open_prs: [prView({ number: 41 })], flow_places: [PLACE], bounces: [bounceView({ recorded_at: fixtureInstant(-7200) })], flow_record: [] });
    }
    return {};
  };
}

describe('DeliveryPage', () => {
  let stub: RequestClientStub;

  function configure(routes = hubRoutes()): void {
    stub = stubRequestClient(hubClient, routes);
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'delivery', component: DeliveryPage }]),
        provideLocationMocks(),
        { provide: FLEET_CLOCK, useValue: () => FIXTURE_NOW },
      ],
    });
  }

  afterEach(() => stub.restore());

  async function open(url = '/delivery'): Promise<{ el: HTMLElement; harness: RouterTestingHarness }> {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return { el: harness.fixture.nativeElement as HTMLElement, harness };
  }

  const listed = (el: HTMLElement) =>
    [...el.querySelectorAll('[data-testid="delivery-row"], [data-testid="delivery-divider"]')].map((node) => node.getAttribute('data-chunk') ?? node.getAttribute('data-testid'));
  const row = (el: HTMLElement, chunkId: string) => el.querySelector<HTMLElement>(`[data-testid="delivery-row"][data-chunk="${chunkId}"]`)!;

  it('holds row-shaped placeholders while its reads are pending', async () => {
    configure();
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/delivery');
    const el = harness.fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="delivery-skeleton"] .skeleton-row').length).toBeGreaterThan(0);
    expect(el.querySelector('[data-testid="delivery-row"]')).toBeNull();
    await settle(harness.fixture);
    expect(el.querySelector('[data-testid="delivery-skeleton"]')).toBeNull();
  });

  it('lists the chunks delivering, newest first, over the divider, then the chunks delivered — and no chunk with no footprint', async () => {
    configure();
    const { el } = await open();

    expect(listed(el)).toEqual([BOUNCED, IN_LINE, 'delivery-divider', LANDED]);
    expect([BOUNCED, IN_LINE, LANDED].map((id) => row(el, id).getAttribute('data-stage'))).toEqual(['-1', '1', '3']);
  });

  it('draws a row\'s place in line, its bounce, its step and its links', async () => {
    configure();
    const { el } = await open();

    const inLine = row(el, IN_LINE);
    expect(inLine.querySelector('.stage.at')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('in line #2');
    expect(inLine.querySelector('.step')?.textContent?.trim()).toBe('adv-dwf/build');
    expect([...inLine.querySelectorAll('[data-testid="delivery-link"]')].map((link) => link.textContent?.replace(/\s+/g, ' ').trim())).toEqual(['blizzard #41']);
    expect(inLine.querySelector('[data-testid="delivery-bounce"]')).toBeNull();

    expect(row(el, BOUNCED).querySelector('[data-testid="delivery-bounce"] .cause')?.textContent?.trim()).toBe('failure [10m 0s]');
    expect(row(el, LANDED).querySelector('[data-testid="delivery-link"]')?.textContent).toContain('9f2c4e1');
  });

  it('says so when the flows cannot be read', async () => {
    configure(hubRoutes(() => stubError(500, { detail: 'down' })));
    const { el } = await open();

    expect(el.querySelector('[data-testid="delivery-error"]')?.textContent).toContain("Failed to read the hub's flows.");
    expect(el.querySelector('[data-testid="delivery-row"]')).toBeNull();
  });

  it('opens the drawer for a picked row through the URL, and closes it on a second pick or Escape', async () => {
    configure();
    const { el, harness } = await open();

    row(el, IN_LINE).querySelector<HTMLElement>('[data-testid="delivery-row-open"]')!.click();
    await settle(harness.fixture);
    expect(TestBed.inject(Router).url).toBe(`/delivery?chunk=${IN_LINE}`);
    expect(row(el, IN_LINE).getAttribute('aria-current')).toBe('true');
    expect(el.querySelector('[data-testid="delivery-drawer"]')).not.toBeNull();

    row(el, IN_LINE).querySelector<HTMLElement>('[data-testid="delivery-row-open"]')!.click();
    await settle(harness.fixture);
    expect(TestBed.inject(Router).url).toBe('/delivery');
    expect(el.querySelector('[data-testid="delivery-drawer"]')).toBeNull();

    await harness.navigateByUrl(`/delivery?chunk=${IN_LINE}`);
    await settle(harness.fixture);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await settle(harness.fixture);
    expect(TestBed.inject(Router).url).toBe('/delivery');
  });

  it('fills the drawer from the chunk\'s own detail: its repositories, summary, and history', async () => {
    configure();
    const { el } = await open(`/delivery?chunk=${IN_LINE}`);

    const drawer = el.querySelector<HTMLElement>('[data-testid="delivery-drawer"]')!;
    expect(drawer.querySelector('[data-testid="delivery-drawer-open"]')?.getAttribute('href')).toBe(`/board/chunk/${IN_LINE}`);
    const repos = [...drawer.querySelectorAll('[data-testid="delivery-drawer-repo"]')];
    expect(repos.map((repo) => repo.querySelector('.repo-state')?.textContent?.trim())).toEqual(['in line · 2nd']);
    expect(drawer.querySelector('[data-testid="delivery-drawer-summary"]')?.textContent?.replace(/\s+/g, ' ')).toContain('1 bounce');
    expect(drawer.querySelectorAll('[data-testid="delivery-history-entry"]')).toHaveLength(1);
  });

  it('draws no drawer for a chunk the list does not hold', async () => {
    configure();
    const { el } = await open(`/delivery?chunk=${IDLE}`);

    expect(el.querySelector('[data-testid="delivery-drawer"]')).toBeNull();
  });
});
