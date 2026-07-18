import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { vi } from 'vitest';

import { LocalPanel } from './local-panel';
import { settle } from './testing/settle';
import { RouteError, type RunnerClientStub, stubRunnerClient } from './testing/stub-runner-client';

/** Matches `GET /api/chunks/{chunk_id}/pm-items` for any chunk id. */
const PM_ITEMS_ROUTE = /^\/api\/chunks\/[^/]+\/pm-items$/;

const REF = Date.parse('2026-07-16T12:00:00.000Z');

const LEASE = (overrides: Record<string, unknown> = {}) => ({
  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-77',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00.000Z',
  last_heartbeat_at: '2026-07-16T11:59:26.000Z',
  state: 'running',
  closed_at: null,
  closure_reason: null,
  ...overrides,
});

/**
 * A route table for the shell's whole read surface. Leases come from `leases`;
 * every other local read defaults to its empty shape so a test that only cares
 * about the lease/chunk panes stays silent about the rails. Override per-path
 * via `extra` (return `undefined` to fall through to the defaults).
 */
function routes(
  leases: unknown[],
  extra: (method: string, path: string) => unknown = () => undefined,
): (method: string, path: string) => unknown {
  return (method, path) => {
    const special = extra(method, path);
    if (special !== undefined) return special;
    if (method !== 'GET') return {};
    if (path === '/api/leases') return { items: leases };
    return { items: [] };
  };
}

async function setUp(): Promise<void> {
  await TestBed.configureTestingModule({
    imports: [LocalPanel],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
}

async function render() {
  await setUp();
  const fixture = TestBed.createComponent(LocalPanel);
  await settle(fixture);
  return fixture;
}

describe('LocalPanel', () => {
  let stub: RunnerClientStub;

  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(REF);
  });

  afterEach(() => {
    stub.restore();
    vi.restoreAllMocks();
  });

  it('reflects the connection input in the header', async () => {
    stub = stubRunnerClient(routes([]));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    fixture.componentRef.setInput('connection', 'ok');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('ok');
  });

  it('shows a loading line before the first read resolves, not the empty state', async () => {
    stub = stubRunnerClient(routes([]));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    // Right after creation the stubbed fetch's promise hasn't resolved yet — the
    // panel must read as loading, never as "idle".
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="loading-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
    await settle(fixture);
  });

  it('renders the genuinely-idle empty state only once the read resolves with zero leases', async () => {
    stub = stubRunnerClient(routes([]));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('NO LIVE LEASES');
    expect(el.querySelector('[data-testid="chunks-empty"]')).not.toBeNull();
  });

  it('shows a distinct degraded line on a 503 — the empty state must never appear on a failed read', async () => {
    stub = stubRunnerClient((method, path) => {
      if (method === 'GET' && path === '/api/leases') throw new RouteError(503);
      return { items: [] };
    });
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="error-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
  });

  it('renders one agent-row per active lease and none for closed ones — the rail is liveness, not history', async () => {
    const closed = LEASE({
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNCCCC',
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD',
      state: 'closed',
      closed_at: '2026-07-16T11:30:00.000Z',
      closure_reason: 'transitioned',
    });
    stub = stubRunnerClient(routes([LEASE(), closed]));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="agent-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute('data-lease-id')).toBe('lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
    expect(el.querySelector('[data-testid="lease-count"]')?.textContent).toContain('1 live');
  });

  it('renders one chunk-row per distinct chunk, including closed history, newest lease first', async () => {
    const older = LEASE({
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB',
      epoch: 1,
      state: 'closed',
      closure_reason: 'failed',
    });
    const otherChunk = LEASE({
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNCCCC',
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYNDDDD',
      state: 'closed',
      closure_reason: 'transitioned',
    });
    stub = stubRunnerClient(routes([LEASE(), otherChunk, older]));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="chunk-row"]');
    // Two distinct chunks — the duplicate (older lease of the first chunk) folds away.
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-chunk-id')).toBe('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(rows[0].querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('RUNNING');
    expect(rows[1].querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('TRANSITIONED');
  });

  it('derives NEEDS HUMAN from an open escalation, outranking the lease state', async () => {
    stub = stubRunnerClient(
      routes([LEASE({ state: 'closed', closure_reason: 'escalated' })], (method, path) =>
        method === 'GET' && path === '/api/escalations'
          ? {
              items: [
                {
                  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                  lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                  node_id: 'nd_build',
                  epoch: 2,
                  closed_at: '2026-07-16T11:45:00.000Z',
                  resume_command: 'cd /ws/beta && claude --resume sess-77',
                },
              ],
            }
          : undefined,
      ),
    );
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunk-row-status"]')?.textContent?.trim()).toBe('NEEDS HUMAN');
  });

  describe('selection drives the machine detail dock', () => {
    it('shows the SELECT A CHUNK placeholder before anything is selected', async () => {
      stub = stubRunnerClient(routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
    });

    it('selecting a chunk row renders its execution facts and fires the transcript read for its lease', async () => {
      stub = stubRunnerClient(routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('C-3YJ9');
      const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
      expect(facts).toContain('L-ZPRR');
      expect(facts).toContain('4821');
      expect(facts).toContain('/ws/beta');
      expect(facts).toContain('sess-77');
      expect(stub.forRoute('/api/leases/lease_01KXKVVF1J3D6H6VYZ3XYNZPRR/transcript', 'GET').length).toBeGreaterThan(
        0,
      );
    });

    it('selecting a lease row selects its chunk — one shared selection across both rails', async () => {
      stub = stubRunnerClient(routes([LEASE()]));
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="agent-row"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('C-3YJ9');
      expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(true);
      expect(el.querySelector('[data-testid="agent-row"]')?.classList.contains('selected')).toBe(true);
    });

    it('shows the escalation resume command in the dock for an escalated selected chunk', async () => {
      stub = stubRunnerClient(
        routes([LEASE({ state: 'closed', closure_reason: 'escalated' })], (method, path) =>
          method === 'GET' && path === '/api/escalations'
            ? {
                items: [
                  {
                    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                    node_id: 'nd_build',
                    epoch: 2,
                    closed_at: '2026-07-16T11:45:00.000Z',
                    resume_command: 'cd /ws/beta && claude --resume sess-77',
                  },
                ],
              }
            : undefined,
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
      await settle(fixture);

      expect(el.querySelector('[data-testid="detail-resume"]')?.textContent).toContain(
        'cd /ws/beta && claude --resume sess-77',
      );
    });
  });

  describe('the PM enrichment stays severable (issue #28)', () => {
    it('renders chunk rows on chunk_id alone when every pm-items read 502s — the panel must not depend on the hub', async () => {
      stub = stubRunnerClient(
        routes([LEASE()], (method, path) => {
          if (method === 'GET' && PM_ITEMS_ROUTE.test(path)) throw new RouteError(502);
          return undefined;
        }),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelectorAll('[data-testid="chunk-row"]')).toHaveLength(1);
      expect(el.querySelector('[data-testid="chunk-row"]')?.textContent).toContain('C-3YJ9');
    });

    it('renders the pointer label as a link to the work item when web_url arrived', async () => {
      stub = stubRunnerClient(
        routes([LEASE()], (method, path) =>
          method === 'GET' && PM_ITEMS_ROUTE.test(path)
            ? {
                items: [
                  {
                    source: 'blizzard',
                    ref: '61',
                    label: 'blizzard#61',
                    web_url: 'https://github.com/paul-gross/blizzard/issues/61',
                    fetched_at: '2026-07-16T11:00:00.000Z',
                    title: 'runner machine panel',
                  },
                ],
              }
            : undefined,
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const link = el.querySelector<HTMLAnchorElement>('[data-testid="chunk-row-title"] a');
      expect(link?.textContent).toContain('blizzard#61');
      expect(link?.href).toBe('https://github.com/paul-gross/blizzard/issues/61');
      expect(el.querySelector('[data-testid="chunk-row-title"]')?.textContent).toContain('runner machine panel');
    });

    it('issues one pm-items request per distinct chunk even with several leases for it', async () => {
      const older = LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNBBBB', epoch: 1, state: 'closed' });
      stub = stubRunnerClient(routes([LEASE(), older]));
      const fixture = await render();
      await settle(fixture);

      const pmRequests = stub.requests.filter((r) => PM_ITEMS_ROUTE.test(r.path));
      expect(pmRequests).toHaveLength(1);
    });
  });

  describe('the right rail', () => {
    it('renders the hub link panel off GET /api/runner, endpoint and board link included', async () => {
      stub = stubRunnerClient(
        routes([], (method, path) =>
          method === 'GET' && path === '/api/runner'
            ? {
                runner_id: 'runner-local',
                workspace_id: 'workspace-local',
                pause: { local: false, hub: false, effective: false },
                capacities: { max_agents: 4, used: 1, free: 3 },
                hub: {
                  endpoint: 'http://127.0.0.1:8421',
                  reachable: true,
                  last_contact_at: '2026-07-16T11:59:30.000Z',
                  buffer_depth: 2,
                },
                last_tick_at: '2026-07-16T11:59:45.000Z',
              }
            : undefined,
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="hub-endpoint"]')?.textContent).toContain('http://127.0.0.1:8421');
      expect(el.querySelector('[data-testid="hub-link"]')?.textContent).toContain('CONNECTED');
      expect(el.querySelector('[data-testid="hub-last-flush"]')?.textContent).toContain('-30s');
      expect(el.querySelector('[data-testid="hub-buffered"]')?.textContent).toContain('2 events');
      expect(el.querySelector<HTMLAnchorElement>('[data-testid="board-link"]')?.href).toBe('http://127.0.0.1:8421/');
    });

    it('renders open asks with their chunk refs and age', async () => {
      stub = stubRunnerClient(
        routes([], (method, path) =>
          method === 'GET' && path === '/api/asks'
            ? {
                items: [
                  {
                    question_id: 'qn_01KXKVVF1J3D6H6VYZ3XYNQ777',
                    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                    question: 'which branch?',
                    options: [],
                    session_id: 'sess-77',
                    asked_at: '2026-07-16T11:40:00.000Z',
                  },
                ],
              }
            : undefined,
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const ask = el.querySelector('[data-testid="ask-row"]');
      expect(ask?.textContent).toContain('C-3YJ9');
      expect(ask?.textContent).toContain('which branch?');
      expect(ask?.textContent).toContain('20m');
    });

    it('renders the fact log off the outbound ledger with flush markers', async () => {
      stub = stubRunnerClient(
        routes([], (method, path) =>
          method === 'GET' && path === '/api/facts'
            ? {
                items: [
                  {
                    seq: 2,
                    kind: 'completion.submitted',
                    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                    created_at: '2026-07-16T11:58:00.000Z',
                    acked_at: null,
                  },
                  {
                    seq: 1,
                    kind: 'lease.minted',
                    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                    lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNZPRR',
                    created_at: '2026-07-16T11:50:00.000Z',
                    acked_at: '2026-07-16T11:50:05.000Z',
                  },
                ],
              }
            : undefined,
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const rows = el.querySelectorAll('[data-testid="fact-row"]');
      expect(rows).toHaveLength(2);
      expect(rows[0].textContent).toContain('completion.submitted');
      expect(rows[0].textContent).toContain('C-3YJ9');
      expect(rows[0].querySelector('.flush')?.classList.contains('acked')).toBe(false);
      expect(rows[1].querySelector('.flush')?.classList.contains('acked')).toBe(true);
    });

    it('renders the held environments with chunk ref and held-for age', async () => {
      stub = stubRunnerClient(
        routes([], (method, path) =>
          method === 'GET' && path === '/api/environments'
            ? {
                items: [
                  {
                    environment_id: 'beta',
                    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
                    held_since: '2026-07-16T11:18:00.000Z',
                  },
                ],
              }
            : undefined,
        ),
      );
      const fixture = await render();
      const el = fixture.nativeElement as HTMLElement;

      const row = el.querySelector('[data-testid="env-row"]');
      expect(row?.getAttribute('data-env-id')).toBe('beta');
      expect(row?.textContent).toContain('C-3YJ9');
      expect(row?.querySelector('[data-testid="env-held-for"]')?.textContent).toContain('42m');
    });
  });
});
