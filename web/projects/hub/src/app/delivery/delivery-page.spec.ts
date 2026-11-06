import { provideLocationMocks } from '@angular/common/testing';
import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { FLEET_CLOCK, type FlowFactView } from 'fleet';
import {
  bounceView,
  FIXTURE_NOW,
  chunkDetail,
  chunkSummary,
  fixtureInstant,
  flowFactView,
  flowView,
  landedRepoView,
  prView,
  repositorySummary,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
} from 'fleet/testing';
import { DeliveryPage } from './delivery-page';

const IN_LINE = 'ch_01inline0000000000000000000';
const BOUNCED = 'ch_01bounced000000000000000000';
const LANDED = 'ch_01landed0000000000000000000';
const IDLE = 'ch_01idle000000000000000000000';

const PLACE = { flow_id: 'fl_a', position: 2, state: 'waiting', branch: 'master', next_poll_at: null, repository_name: 'blizzard', repository_slug: null, resource: 'rp_a:master' } as const;

const CHUNKS = [
  chunkSummary({ chunk_id: IN_LINE, graph_id: 'gr_1', graph_name: 'adv-dwf', status: 'running', current_node_id: 'nd_build', current_node_name: 'build', open_prs: [prView({ number: 41 })], flow_places: [PLACE] }),
  chunkSummary({
    chunk_id: BOUNCED,
    graph_id: 'gr_1',
    status: 'needs_human',
    latest_bounce: bounceView({ cause: 'failure', envelope: JSON.stringify({ cause: 'failure', detail: 'checks failed in `lint`', unlanded: [] }), recorded_at: fixtureInstant(-600) }),
    bounce_count: 1,
  }),
  chunkSummary({ chunk_id: LANDED, graph_id: 'gr_1', status: 'done', terminal: true, landed: true, landed_repos: [landedRepoView()], completed_at: fixtureInstant(-3 * 3600) }),
  chunkSummary({ chunk_id: IDLE, graph_id: 'gr_1', status: 'ready' }),
];

const fact = (id: number, chunkId: string, kind: FlowFactView['kind'], secondsAgo: number, outcome: string | null = null) =>
  flowFactView({
    id,
    flow_id: 'fl_a',
    chunk_id: chunkId,
    kind,
    at: fixtureInstant(-secondsAgo),
    outcome,
    revision: null,
    submission_id: `sub-${chunkId}`,
    verdict: kind === 'turn-ended' ? 'bounced' : null,
  });

type HubRoutes = Parameters<typeof stubHubClient>[0];

/** The page's reads. `flows` answers the active-flows read; `overrides` replaces any route. */
function hubRoutes(
  flows: NonNullable<HubRoutes['GET /api/flows']> = () => ({ flows: [flowView({ flow_id: 'fl_a', repository_id: 'rp_a', line: [], resource: 'rp_a:master', branch: 'master' })] }),
  overrides: HubRoutes = {},
): HubRoutes {
  return {
    'GET /api/flows': flows,
    'GET /api/repositories': () => ({ repositories: [repositorySummary({ repository_id: 'rp_a', owner: 'paul-gross', repo: 'blizzard', name: 'blizzard' })] }),
    'GET /api/chunks': () => ({ chunks: CHUNKS, next_cursor: null }),
    'GET /api/flows/{flow_id}/record': () => ({
      facts: [fact(3, BOUNCED, 'turn-ended', 600, 'failure'), fact(2, IN_LINE, 'submitted', 1800), fact(1, LANDED, 'landed', 7200)],
      next_before: null,
    }),
    'GET /api/work-items': () => ({ chunks: {} }),
    'GET /api/chunks/{chunk_id}': (_request, { chunk_id }) =>
      chunk_id === IN_LINE
        ? chunkDetail({ chunk_id: IN_LINE, open_prs: [prView({ number: 41 })], flow_places: [PLACE], bounces: [bounceView({ recorded_at: fixtureInstant(-7200) })], flow_record: [] })
        : stubError(404, { detail: `unknown chunk ${chunk_id}` }),
    ...overrides,
  };
}

describe('DeliveryPage', () => {
  let stub: RequestClientStub;

  function configure(routes = hubRoutes()): void {
    stub = stubHubClient(routes);
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

    const bounce = row(el, BOUNCED).querySelector('[data-testid="delivery-bounce"]');
    expect(bounce?.querySelector('.cause')?.textContent?.trim()).toBe('failure · [10m 0s]');
    expect(bounce?.querySelector('.cause')?.textContent).not.toContain('withdrawn');
    expect(bounce?.querySelector('.reason')?.textContent?.trim()).toBe('checks failed in lint');
    expect(row(el, LANDED).querySelector('[data-testid="delivery-link"]')?.textContent).toContain('9f2c4e1');
  });

  it('says so when nothing is delivering or delivered', async () => {
    configure(hubRoutes(undefined, { 'GET /api/chunks': () => ({ chunks: [], next_cursor: null }) }));
    const { el } = await open();

    expect(el.querySelector('[data-testid="delivery-empty"]')?.textContent).toContain('Nothing delivering or delivered.');
    expect(el.querySelector('[data-testid="delivery-divider"]')).toBeNull();
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

  it('fills the drawer from the chunk\'s own detail: its repositories and history', async () => {
    configure();
    const { el } = await open(`/delivery?chunk=${IN_LINE}`);

    const drawer = el.querySelector<HTMLElement>('[data-testid="delivery-drawer"]')!;
    expect(drawer.querySelector('[data-testid="detail-id"]')).not.toBeNull();
    const repos = [...drawer.querySelectorAll('[data-testid="delivery-drawer-repo"]')];
    expect(repos.map((repo) => repo.querySelector('.repo-state')?.textContent?.trim())).toEqual(['in line · 2nd']);
    const history = [...drawer.querySelectorAll('[data-testid="delivery-history-entry"] .text')].map((text) => text.textContent?.trim());
    expect(history).toEqual([expect.stringMatching(/^bounced · /)]);
  });

  it('reads no chunk\'s own detail for the list', async () => {
    configure();
    await open();

    expect(stub.forRoute(`/api/chunks/${IN_LINE}`, 'GET')).toHaveLength(0);
    expect(stub.forRoute(`/api/chunks/${BOUNCED}`, 'GET')).toHaveLength(0);
  });

  it('draws no drawer for a chunk the list does not hold', async () => {
    configure();
    const { el } = await open(`/delivery?chunk=${IDLE}`);

    expect(el.querySelector('[data-testid="delivery-drawer"]')).toBeNull();
  });
});
