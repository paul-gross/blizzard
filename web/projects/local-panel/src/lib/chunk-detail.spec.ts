import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { MachineDetail } from './chunk-detail';

/** A `ChunkHeaderView` fixture — the runner's own `GET /api/chunks/{id}` pass-through
 * proxy response the header renders off. */
const HEADER = (overrides: Partial<runnerApi.ChunkHeaderView> = {}): runnerApi.ChunkHeaderView => ({
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  status: 'running',
  work_refs: [{ source: 'blizzard', ref: '185', label: 'blizzard#185', web_url: 'https://forge.example/issues/185' }],
  pause: null,
  ...overrides,
});

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
const CHUNK_ROUTE = /^\/api\/chunks\/([^/]+)$/;
const PAUSE_ROUTE = /^\/api\/chunks\/([^/]+)\/pause$/;
const RESUME_ROUTE = /^\/api\/chunks\/([^/]+)\/resume$/;

/** The header's own severable read + mutation routes, layered onto the transcript
 * route every test needs — `header` answers `GET /api/chunks/{id}`, `pauseResult`
 * answers both `POST .../pause` and `POST .../resume` (a `ChunkSummary`, or a
 * {@link stubError} to exercise the 409 refusal). */
function routes(header: runnerApi.ChunkHeaderView = HEADER(), pauseResult: unknown = {}) {
  return (method: string, path: string): unknown => {
    if (method === 'GET') {
      const transcript = TRANSCRIPT_ROUTE.exec(path);
      if (transcript) {
        return { lease_id: transcript[1], session_id: 'sess', available: true, reason: null, truncated: false, turns: [] };
      }
      if (CHUNK_ROUTE.test(path)) return header;
    }
    if (method === 'POST' && (PAUSE_ROUTE.test(path) || RESUME_ROUTE.test(path))) return pauseResult;
    return {};
  };
}

async function render(
  leases: readonly runnerApi.LeaseView[],
  activeAttemptLeaseId: string | null = null,
  header: runnerApi.ChunkHeaderView = HEADER(),
): Promise<{
  el: HTMLElement;
  fixture: ComponentFixture<MachineDetail>;
  stub: RequestClientStub;
}> {
  const stub = stubRequestClient(runnerClient, routes(header));
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

/**
 * The header shape (issue #185) — matches the hub board's own chunk-detail header:
 * the full chunk id, work items as links, the derived state, a working Pause/Resume,
 * and a close button. Pause/Resume and the work-item links are the dock's own
 * severable read ({@link injectChunkDetailQuery}); these specs drive that read
 * through the stubbed `GET /api/chunks/{id}` route rather than the container-fed
 * `leases`/`status` inputs the attempt-tab specs above cover.
 */
describe('MachineDetail header', () => {
  let stub: RequestClientStub;

  afterEach(() => {
    stub.restore();
    vi.restoreAllMocks();
  });

  it('shows the full chunk id, not the compact shortname or a "chunk detail" label', async () => {
    const rendered = await render([NEWEST()]);
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toBe(NEWEST().chunk_id);
    expect(el.textContent).not.toContain('chunk detail');
  });

  it('renders work items as links out to their web url', async () => {
    const rendered = await render([NEWEST()]);
    stub = rendered.stub;
    const { el } = rendered;

    const pointer = el.querySelector<HTMLAnchorElement>('[data-testid="detail-pointer"]');
    expect(pointer?.textContent).toContain('blizzard#185');
    expect(pointer?.getAttribute('href')).toBe('https://forge.example/issues/185');
  });

  it('degrades a work item with no web url to plain text, not a broken link', async () => {
    const header = HEADER({ work_refs: [{ source: 'blizzard', ref: '185', label: 'blizzard#185', web_url: null }] });
    const rendered = await render([NEWEST()], null, header);
    stub = rendered.stub;
    const { el } = rendered;

    const pointer = el.querySelector('[data-testid="detail-pointer"]');
    expect(pointer?.tagName).toBe('SPAN');
    expect(pointer?.textContent).toContain('blizzard#185');
  });

  it('shows the derived chunk state', async () => {
    const rendered = await render([NEWEST()]);
    stub = rendered.stub;
    const { el, fixture } = rendered;
    fixture.componentRef.setInput('status', { label: 'RUNNING', tone: 'running' });
    await settle(fixture);

    expect(el.querySelector('[data-testid="machine-detail-status"]')?.textContent).toContain('RUNNING');
  });

  it('emits dismiss when the close button is clicked', async () => {
    const rendered = await render([NEWEST()]);
    stub = rendered.stub;
    const { el, fixture } = rendered;
    let dismissed = false;
    fixture.componentInstance.dismiss.subscribe(() => (dismissed = true));

    el.querySelector<HTMLElement>('[data-testid="detail-close"]')?.click();

    expect(dismissed).toBe(true);
  });

  it('offers no detach button', async () => {
    const rendered = await render([NEWEST()]);
    stub = rendered.stub;
    expect(rendered.el.querySelector('[data-testid="detach-chunk"]')).toBeNull();
  });

  it('offers Pause for an unpaused, pausable chunk and fires the mutation once confirmed', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const rendered = await render([NEWEST()], null, HEADER({ status: 'running', pause: null }));
    stub = rendered.stub;
    const { el, fixture } = rendered;

    expect(el.querySelector('[data-testid="resume-chunk"]')).toBeNull();
    el.querySelector<HTMLElement>('[data-testid="pause-chunk"]')?.click();
    await settle(fixture);

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(stub.forRoute(`/api/chunks/${NEWEST().chunk_id}/pause`, 'POST')).toHaveLength(1);
  });

  it('offers Resume for a paused chunk and fires the mutation once confirmed', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(true);
    const rendered = await render(
      [NEWEST()],
      null,
      HEADER({ status: 'paused', pause: { by: 'operator', set_at: '2026-07-16T11:00:00.000Z' } }),
    );
    stub = rendered.stub;
    const { el, fixture } = rendered;

    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();
    el.querySelector<HTMLElement>('[data-testid="resume-chunk"]')?.click();
    await settle(fixture);

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(stub.forRoute(`/api/chunks/${NEWEST().chunk_id}/resume`, 'POST')).toHaveLength(1);
  });

  it('does not fire the pause mutation when the operator declines the confirm', async () => {
    const confirmSpy = vi.spyOn(globalThis, 'confirm').mockReturnValue(false);
    const rendered = await render([NEWEST()], null, HEADER({ status: 'running', pause: null }));
    stub = rendered.stub;
    const { el, fixture } = rendered;

    el.querySelector<HTMLElement>('[data-testid="pause-chunk"]')?.click();
    await settle(fixture);

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(stub.forRoute(`/api/chunks/${NEWEST().chunk_id}/pause`, 'POST')).toHaveLength(0);
  });

  it('offers neither Pause nor Resume for a chunk in a non-pausable state', async () => {
    const rendered = await render([NEWEST()], null, HEADER({ status: 'done', pause: null }));
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();
    expect(el.querySelector('[data-testid="resume-chunk"]')).toBeNull();
  });
});
