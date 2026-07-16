import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { LocalPanel } from './local-panel';
import { settle } from './testing/settle';
import { RouteError, type RunnerClientStub, stubRunnerClient } from './testing/stub-runner-client';

const LEASE = (overrides: Record<string, unknown> = {}) => ({
  lease_id: 'L-903',
  chunk_id: 'C-125',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 2,
  session_id: 'sess-77',
  pid: 4821,
  environment_id: 'beta',
  workdir: '/ws/beta',
  created_at: '2026-07-16T11:00:00+00:00',
  last_heartbeat_at: '2026-07-16T11:59:26+00:00',
  state: 'running',
  ...overrides,
});

async function setUp(): Promise<void> {
  await TestBed.configureTestingModule({
    imports: [LocalPanel],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
}

describe('LocalPanel', () => {
  let stub: RunnerClientStub;

  afterEach(() => stub.restore());

  it('reflects the connection input in the header', async () => {
    stub = stubRunnerClient((method, path) => (method === 'GET' && path === '/api/leases' ? { items: [] } : {}));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    fixture.componentRef.setInput('connection', 'ok');
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('ok');
  });

  it('shows a loading line before the first read resolves, not the empty state', async () => {
    stub = stubRunnerClient((method, path) => (method === 'GET' && path === '/api/leases' ? { items: [] } : {}));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    // Right after creation the stubbed fetch's promise hasn't resolved yet — the
    // query is still pending, so the loading line (not IDLE) must be showing.
    fixture.detectChanges();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="loading-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();

    await settle(fixture);
    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('RUNNER IDLE');
  });

  it('renders the genuinely-idle empty state only once the read resolves with zero leases', async () => {
    stub = stubRunnerClient((method, path) => (method === 'GET' && path === '/api/leases' ? { items: [] } : {}));
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="empty-state"]')?.textContent).toContain('RUNNER IDLE');
    expect(el.querySelector('[data-testid="error-state"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="agent-row"]')).toHaveLength(0);
  });

  it('renders one agent-row per active lease from GET /api/leases', async () => {
    stub = stubRunnerClient((method, path) =>
      method === 'GET' && path === '/api/leases'
        ? { items: [LEASE(), LEASE({ lease_id: 'L-905', chunk_id: 'C-126', state: 'stale' })] }
        : {},
    );
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="agent-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].getAttribute('data-lease-id')).toBe('L-903');
    expect(rows[1].getAttribute('data-lease-id')).toBe('L-905');
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
  });

  it('shows a distinct degraded line on a 503 — the empty state must never appear on a failed read', async () => {
    stub = stubRunnerClient((method, path) => {
      if (method === 'GET' && path === '/api/leases') throw new RouteError(503, 'lease store not wired');
      return {};
    });
    await setUp();
    const fixture = TestBed.createComponent(LocalPanel);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="error-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
    expect(el.querySelectorAll('[data-testid="agent-row"]')).toHaveLength(0);
  });
});
