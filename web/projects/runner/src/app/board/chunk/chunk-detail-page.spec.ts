import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';

import { ChunkDetailPage } from './chunk-detail-page';

/**
 * The runner-local chunk detail page (`/board/chunk/:chunkId`, issue #318,
 * tabbed follow-up) — driven through a real router (`RouterTestingHarness`)
 * rather than a stubbed `ActivatedRoute`: the page reads its own route param
 * (`:chunkId`) and two independent query params (`?tab=`, `?attempt=`) and
 * renders a `routerLink`-free but genuinely routed page, so the URL round
 * trip is part of what is under test.
 *
 * The runner client's transport is stubbed, so this asserts what the page
 * composes off known reads, not the queries themselves (those have their
 * own specs).
 */
const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';
const NEW_LEASE = 'lease_01KXKVVF1J3D6H6VYZ3XYNNEW1';
const OLD_LEASE = 'lease_01KXKVVF1J3D6H6VYZ3XYNOLD1';

const DETAIL = {
  chunk_id: CHUNK_ID,
  graph_id: 'gr_1',
  graph_name: 'default',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 2,
  status: 'running',
  work_refs: [{ source: 'blizzard', ref: '318', label: 'blizzard#318', web_url: 'https://forge.example/issues/318' }],
  history: [
    {
      choice_name: 'pass',
      epoch: 1,
      from_node_id: 'nd_build',
      from_node_name: 'build',
      graph_id: 'gr_1',
      graph_name: 'default',
      recorded_at: '2026-07-16T11:00:00.000Z',
      to_node_id: 'nd_review',
      to_node_name: 'review',
    },
  ],
  artifacts: [
    {
      key: 'build.retrospective.1',
      kind: 'asset',
      name: 'retrospective',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      content: 'went fine',
      recorded_at: '2026-07-16T11:10:00.000Z',
    },
  ],
};

const NEWEST = {
  lease_id: NEW_LEASE,
  chunk_id: CHUNK_ID,
  graph_id: 'gr_1',
  node_id: 'nd_review',
  node_name: 'review',
  epoch: 2,
  session_id: 'sess-new',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running',
  closed_at: null,
  closure_reason: null,
};

const OLDER = { ...NEWEST, lease_id: OLD_LEASE, epoch: 1, session_id: 'sess-old', state: 'closed', closure_reason: 'failed' };

/** Stands in for the board route — only its resolving matters here. */
@Component({ selector: 'app-board-stub', template: '' })
class BoardStub {}

const ROUTES = [
  { path: 'board', component: BoardStub },
  { path: 'board/chunk/:chunkId', component: ChunkDetailPage },
];

function routes(leases: readonly unknown[] = [NEWEST]): (method: string, path: string) => unknown {
  return (method, path) => {
    if (method !== 'GET') return {};
    if (path === `/api/chunks/${CHUNK_ID}`) return DETAIL;
    if (path === `/api/chunks/${CHUNK_ID}/work-items`) {
      return { items: [{ source: 'blizzard', ref: '318', label: 'blizzard#318', title: 'Chunk detail route', body: 'x', comments: [] }] };
    }
    if (path === '/api/leases') return { items: leases };
    const transcriptMatch = /^\/api\/leases\/([^/]+)\/transcript$/.exec(path);
    if (transcriptMatch) {
      return { lease_id: transcriptMatch[1], session_id: 'sess', available: true, reason: null, truncated: false, turns: [] };
    }
    return {};
  };
}

describe('ChunkDetailPage', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRequestClient(runnerClient, routes());
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter(ROUTES),
      ],
    });
  });

  afterEach(() => stub.restore());

  async function open(url: string): Promise<HTMLElement> {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    return harness.fixture.nativeElement as HTMLElement;
  }

  it('renders work item, issues, node history, and asks · decisions on the General tab, active by default', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    expect(el.querySelector('[data-testid="chunk-detail-page"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="tab-general"]')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('fleet-chunk-detail-facts')).not.toBeNull();
    expect(el.querySelector('fleet-chunk-detail-issue-pane')).not.toBeNull();
    expect(el.querySelector('fleet-chunk-detail-timeline')).not.toBeNull();
    expect(el.querySelector('fleet-chunk-detail-awaiting-human')).not.toBeNull();
    expect(el.querySelector('fleet-chunk-artifacts-panel')).toBeNull();
  });

  it('gains the identity header the hub’s own chunk page carries, naming the chunk by its full id', async () => {
    // This page never had one before this refactor — the shared
    // `fleet-chunk-page-header` (`ChunkPageShell`'s composition) — so this pins
    // both that it now exists and that it reads the full id, not a compact ref.
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    const ref = el.querySelector('[data-testid="mobile-chunk-ref"]');
    expect(ref?.textContent?.trim()).toBe(CHUNK_ID);
    expect(el.querySelector('[data-testid="mobile-chunk-status"]')?.textContent?.trim()).toBe('running');
  });

  it('renders artifacts on the Artifacts tab, not on the default General tab', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=artifacts`);

    expect(el.querySelector('[data-testid="tab-artifacts"]')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('fleet-chunk-artifacts-panel')).not.toBeNull();
    expect(el.querySelector('[data-testid="artifacts-panel-nav-item"]')?.getAttribute('data-artifact-key')).toBe(
      'build.retrospective.1',
    );
    expect(el.querySelector('[data-testid="section-work-item"]')).toBeNull();
  });

  it('picking an artifact nav row writes ?artifact= and switches to the Artifacts tab', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=artifacts`);
    await settle(harness.fixture);
    let el = harness.fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="artifacts-panel-nav-item"]')?.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;

    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=artifacts&artifact=build.retrospective.1`);
    expect(el.querySelector('[data-testid="artifacts-panel-nav-item"]')?.classList.contains('active')).toBe(true);
  });

  it('renders the Node history tab with the shared timeline, row activation on, and no transcript pane', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=node-history`);

    expect(el.querySelector('[data-testid="tab-node-history"]')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('[data-testid="chunk-node-history-tab"]')).not.toBeNull();
    const row = el.querySelector('[data-testid="selection-step"]');
    expect(row?.getAttribute('role')).toBe('button');
    expect(el.querySelector('[data-testid^="node-history-transcript"]')).toBeNull();
  });

  it('activating a node-history row writes ?step= and shows that step’s own artifacts', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=node-history`);
    await settle(harness.fixture);
    let el = harness.fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="selection-step"]')?.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;

    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=node-history&step=nd_build:1`);
    expect(el.querySelector('[data-testid="node-history-artifact-key"]')?.textContent).toContain('build.retrospective.1');
  });

  it('switches tabs on click, writing ?tab= with no full reload, and keeps ?attempt= across the switch', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?attempt=${NEW_LEASE}`);
    await settle(harness.fixture);
    let el = harness.fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>('[data-testid="tab-transcripts"]')?.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;

    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?attempt=${NEW_LEASE}&tab=transcripts`);
    expect(el.querySelector('[data-testid="tab-transcripts"]')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('[data-testid="section-transcript"]')).not.toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="tab-general"]')?.click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;

    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?attempt=${NEW_LEASE}&tab=general`);
    expect(el.querySelector('[data-testid="section-work-item"]')).not.toBeNull();
  });

  it('defaults to the General tab for a garbage ?tab value', async () => {
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=not-a-real-tab`);

    expect(el.querySelector('[data-testid="tab-general"]')?.getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('[data-testid="section-work-item"]')).not.toBeNull();
  });

  it('renders the transcript for the newest attempt by default on the Transcripts tab', async () => {
    stub.restore();
    stub = stubRequestClient(runnerClient, routes([NEWEST, OLDER]));
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=transcripts`);

    expect(stub.forRoute(`/api/leases/${NEW_LEASE}/transcript`, 'GET').length).toBeGreaterThan(0);
    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(tabs).toHaveLength(2);
    expect(tabs[1].getAttribute('aria-pressed')).toBe('true');
  });

  it('hydrates the requested attempt from ?attempt= when it names one of the chunk\u2019s leases', async () => {
    stub.restore();
    stub = stubRequestClient(runnerClient, routes([NEWEST, OLDER]));
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=transcripts&attempt=${OLD_LEASE}`);

    expect(stub.forRoute(`/api/leases/${OLD_LEASE}/transcript`, 'GET').length).toBeGreaterThan(0);
    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(tabs[0].getAttribute('aria-pressed')).toBe('true');
  });

  it('writes an attempt pick to ?attempt= with no full reload, keeping the chunk id in the path', async () => {
    stub.restore();
    stub = stubRequestClient(runnerClient, routes([NEWEST, OLDER]));
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(`/board/chunk/${CHUNK_ID}?tab=transcripts`);
    await settle(harness.fixture);
    let el = harness.fixture.nativeElement as HTMLElement;

    el.querySelectorAll<HTMLElement>('[data-testid="attempt-tab"]')[0].click();
    await settle(harness.fixture);
    el = harness.fixture.nativeElement as HTMLElement;

    expect(TestBed.inject(Router).url).toBe(`/board/chunk/${CHUNK_ID}?tab=transcripts&attempt=${OLD_LEASE}`);
    expect(el.querySelectorAll('[data-testid="attempt-tab"]')[0].getAttribute('aria-pressed')).toBe('true');
  });

  it('renders an empty state for a chunk with no recorded leases, gated on the resolved read', async () => {
    stub.restore();
    stub = stubRequestClient(runnerClient, routes([]));
    const el = await open(`/board/chunk/${CHUNK_ID}?tab=transcripts`);

    expect(el.querySelector('[data-testid="attempts-empty"]')?.textContent).toContain('NO RECENT ATTEMPTS');
    expect(el.querySelector('local-transcript-panel')).toBeNull();
  });

  it('renders the open escalation through the shared awaiting-human section', async () => {
    // The proxied aggregate carries `escalation` (issue #314), so a needs_human chunk
    // reads the same on this route as on the hub board. Asserting the takeover command
    // itself, not just that the section mounts: an aggregate that dropped the field
    // would mount an always-empty sub-panel and satisfy a mount-only assertion.
    stub.restore();
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === `/api/chunks/${CHUNK_ID}`) {
        return {
          ...DETAIL,
          status: 'needs_human',
          escalation: { epoch: 2, takeover_command: `cd /ws/beta && claude --resume sess-new`, wrapped_takeover_command: '' },
        };
      }
      return routes()(method, path);
    });
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    const escalation = el.querySelector('fleet-chunk-detail-awaiting-human [data-testid="escalation"]');
    expect(escalation).not.toBeNull();
    expect(el.querySelector('[data-testid="takeover-command"]')?.textContent).toContain('claude --resume sess-new');
  });

  it('sends the back link to the board carrying only its ?chunk= selection', async () => {
    // `?attempt=` is this route's own param — the board has no attempt selection to
    // restore it into, so the back link never writes one.
    const el = await open(`/board/chunk/${CHUNK_ID}?attempt=${NEW_LEASE}`);

    const back = el.querySelector<HTMLAnchorElement>('[data-testid="chunk-detail-back"]');
    expect(back?.getAttribute('href')).toBe(`/board?chunk=${CHUNK_ID}`);
  });

  it('shows an error state when the chunk detail read fails', async () => {
    stub.restore();
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === `/api/chunks/${CHUNK_ID}`) return stubError(502, { detail: 'unreachable' });
      return routes()(method, path);
    });
    const el = await open(`/board/chunk/${CHUNK_ID}`);

    expect(el.querySelector('[data-testid="chunk-detail-page-error"]')).not.toBeNull();
    expect(el.querySelector('fleet-chunk-detail-facts')).toBeNull();
  });
});
