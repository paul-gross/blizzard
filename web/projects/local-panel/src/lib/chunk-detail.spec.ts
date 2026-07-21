import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';

import { MachineDetail } from './chunk-detail';

const NEW_LEASE = 'lease_01KXKVVF1J3D6H6VYZ3XYNNEW1';
const OLD_LEASE = 'lease_01KXKVVF1J3D6H6VYZ3XYNOLD1';

/** A running newest attempt (epoch 2) — the chunk's freshest lease. */
const NEWEST = (overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView => ({
  lease_id: NEW_LEASE,
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  node_id: 'nd_build',
  node_name: 'build',
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
  ...overrides,
});

/** An older, failed attempt (epoch 1) of the same chunk. */
const OLDER = (overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView =>
  NEWEST({
    lease_id: OLD_LEASE,
    epoch: 1,
    session_id: 'sess-old',
    state: 'closed',
    closed_at: '2026-07-16T10:30:00.000Z',
    closure_reason: 'failed',
    ...overrides,
  });

/** A transcript route that answers every lease's read with an empty transcript. */
const TRANSCRIPT_ROUTE = /^\/api\/leases\/([^/]+)\/transcript$/;
function transcripts(method: string, path: string): unknown {
  const match = method === 'GET' ? TRANSCRIPT_ROUTE.exec(path) : null;
  if (match) return { lease_id: match[1], session_id: 'sess', available: true, reason: null, truncated: false, turns: [] };
  return {};
}

async function render(
  leases: readonly runnerApi.LeaseView[],
  activeAttemptLeaseId: string | null = null,
): Promise<{
  el: HTMLElement;
  fixture: ComponentFixture<MachineDetail>;
  stub: RequestClientStub;
}> {
  const stub = stubRequestClient(runnerClient, transcripts);
  await TestBed.configureTestingModule({
    imports: [MachineDetail],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(MachineDetail);
  fixture.componentRef.setInput('leases', leases);
  fixture.componentRef.setInput('activeAttemptLeaseId', activeAttemptLeaseId);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture, stub };
}

/**
 * `MachineDetail` is presentational for attempt selection (issue #99): the
 * container ({@link LocalPanel}) owns which attempt applies — URL-derived, with
 * the fall-back-to-newest rules — and feeds it in as `activeAttemptLeaseId`; the
 * dock renders whichever tab that names and emits `selectAttempt` on a pick.
 * These specs cover that rendering contract; the selection *behavior* (poll-refresh
 * survival, age-out, chunk-change reset, URL round-trip) lives in `local-panel.spec.ts`.
 */
describe('MachineDetail attempt tabs', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  it('shows SELECT A CHUNK and no tab row when nothing is selected', async () => {
    const rendered = await render([]);
    stub = rendered.stub;
    const { el } = rendered;
    expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
    expect(el.querySelector('[data-testid="attempt-tabs"]')).toBeNull();
  });

  it('renders one tab per attempt, oldest to newest, labelled by ordinal and state', async () => {
    const rendered = await render([OLDER(), NEWEST()], NEW_LEASE);
    stub = rendered.stub;
    const { el } = rendered;

    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(tabs).toHaveLength(2);
    // Oldest first (epoch ordinal + closure reason), newest last (ordinal + live state).
    expect(tabs[0].textContent).toContain('a1');
    expect(tabs[0].textContent).toContain('failed');
    expect(tabs[1].textContent).toContain('a2');
    expect(tabs[1].textContent).toContain('running');
  });

  it('marks the tab named by activeAttemptLeaseId active and reads that attempt’s transcript', async () => {
    const rendered = await render([OLDER(), NEWEST()], OLD_LEASE);
    stub = rendered.stub;
    const { el } = rendered;

    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(tabs[0].getAttribute('aria-pressed')).toBe('true');
    expect(tabs[1].getAttribute('aria-pressed')).toBe('false');
    // The named attempt's transcript is the one read.
    expect(stub.forRoute(`/api/leases/${OLD_LEASE}/transcript`, 'GET').length).toBeGreaterThan(0);
  });

  it('emits selectAttempt with the picked attempt lease id when its tab is activated', async () => {
    const rendered = await render([OLDER(), NEWEST()], NEW_LEASE);
    stub = rendered.stub;
    const { el, fixture } = rendered;
    let picked: string | undefined;
    fixture.componentInstance.selectAttempt.subscribe((id) => (picked = id));

    el.querySelectorAll<HTMLElement>('[data-testid="attempt-tab"]')[0].click();
    await settle(fixture);

    // The dock reports the pick upward; it does not re-derive its own selection.
    expect(picked).toBe(OLD_LEASE);
  });

  it('keeps the summary on the newest attempt regardless of the active attempt', async () => {
    const rendered = await render([OLDER(), NEWEST()], OLD_LEASE);
    stub = rendered.stub;
    const { el } = rendered;

    // Summary facts still name the newest attempt, not the active older one.
    const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
    expect(facts).toContain(NEW_LEASE);
    expect(facts).toContain('sess-new');
    expect(facts).not.toContain('sess-old');
  });

  it('renders no tab selector for a single-attempt chunk, but still reads its transcript', async () => {
    const rendered = await render([NEWEST()], NEW_LEASE);
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="attempt-tabs"]')).toBeNull();
    expect(el.querySelector('[data-testid="detail-facts"]')?.textContent).toContain(NEW_LEASE);
    expect(stub.forRoute(`/api/leases/${NEW_LEASE}/transcript`, 'GET').length).toBeGreaterThan(0);
  });
});
