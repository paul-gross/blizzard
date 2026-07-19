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

async function render(leases: readonly runnerApi.LeaseView[]): Promise<{
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
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture, stub };
}

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

  it('renders one tab per attempt, oldest to newest, newest selected by default', async () => {
    const rendered = await render([OLDER(), NEWEST()]);
    stub = rendered.stub;
    const { el } = rendered;

    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(tabs).toHaveLength(2);
    // Oldest first, newest last (labelled by attempt ordinal).
    expect(tabs[0].textContent).toContain('a1');
    expect(tabs[1].textContent).toContain('a2');
    // Newest selected by default.
    expect(tabs[1].getAttribute('aria-pressed')).toBe('true');
    expect(tabs[0].getAttribute('aria-pressed')).toBe('false');
    // Default transcript read is the newest attempt's.
    expect(stub.forRoute(`/api/leases/${NEW_LEASE}/transcript`, 'GET').length).toBeGreaterThan(0);
  });

  it('labels each tab with its attempt ordinal and state so attempts are distinguishable', async () => {
    const rendered = await render([OLDER(), NEWEST()]);
    stub = rendered.stub;
    const { el } = rendered;

    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    // epoch ordinal + closure reason on the closed older attempt…
    expect(tabs[0].textContent).toContain('a1');
    expect(tabs[0].textContent).toContain('failed');
    // …and epoch ordinal + live state on the running newest attempt.
    expect(tabs[1].textContent).toContain('a2');
    expect(tabs[1].textContent).toContain('running');
  });

  it('switches the transcript to a prior attempt when its tab is selected', async () => {
    const rendered = await render([OLDER(), NEWEST()]);
    stub = rendered.stub;
    const { el, fixture } = rendered;

    el.querySelectorAll<HTMLElement>('[data-testid="attempt-tab"]')[0].click();
    await settle(fixture);

    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(tabs[0].getAttribute('aria-pressed')).toBe('true');
    expect(tabs[1].getAttribute('aria-pressed')).toBe('false');
    // The older attempt's transcript is now read.
    expect(stub.forRoute(`/api/leases/${OLD_LEASE}/transcript`, 'GET').length).toBeGreaterThan(0);
  });

  it('keeps the summary on the newest attempt regardless of which tab is selected', async () => {
    const rendered = await render([OLDER(), NEWEST()]);
    stub = rendered.stub;
    const { el, fixture } = rendered;

    el.querySelectorAll<HTMLElement>('[data-testid="attempt-tab"]')[0].click();
    await settle(fixture);

    // Summary facts still name the newest attempt, not the selected older one.
    const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
    expect(facts).toContain(NEW_LEASE);
    expect(facts).toContain('sess-new');
    expect(facts).not.toContain('sess-old');
  });

  it('renders no tab selector for a single-attempt chunk, but still reads its transcript', async () => {
    const rendered = await render([NEWEST()]);
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="attempt-tabs"]')).toBeNull();
    expect(el.querySelector('[data-testid="detail-facts"]')?.textContent).toContain(NEW_LEASE);
    expect(stub.forRoute(`/api/leases/${NEW_LEASE}/transcript`, 'GET').length).toBeGreaterThan(0);
  });

  it('keeps the picked attempt selected across a poll refresh of the same chunk', async () => {
    const rendered = await render([OLDER(), NEWEST()]);
    stub = rendered.stub;
    const { el, fixture } = rendered;

    // Operator picks the older attempt…
    el.querySelectorAll<HTMLElement>('[data-testid="attempt-tab"]')[0].click();
    await settle(fixture);

    // …then the leases list re-fetches (poll refresh) with fresh objects, same ids.
    fixture.componentRef.setInput('leases', [OLDER(), NEWEST()]);
    await settle(fixture);

    // The pick survives — the older attempt is still selected, not reset to newest.
    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(tabs[0].getAttribute('aria-pressed')).toBe('true');
    expect(tabs[1].getAttribute('aria-pressed')).toBe('false');
  });

  it('falls back to the newest attempt when the picked attempt ages out of the window', async () => {
    const rendered = await render([OLDER(), NEWEST()]);
    stub = rendered.stub;
    const { el, fixture } = rendered;

    // Pick the older attempt, then it drops out of the recent-lease window as a
    // newer attempt (epoch 3) appears.
    el.querySelectorAll<HTMLElement>('[data-testid="attempt-tab"]')[0].click();
    await settle(fixture);
    const NEWER = NEWEST({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNEW3', epoch: 3, session_id: 'sess-new3' });
    fixture.componentRef.setInput('leases', [NEWEST(), NEWER]);
    await settle(fixture);

    // Selection resets to the newest attempt (a3), not the vanished pick.
    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(tabs[1].textContent).toContain('a3');
    expect(tabs[1].getAttribute('aria-pressed')).toBe('true');
    expect(tabs[0].getAttribute('aria-pressed')).toBe('false');
    expect(stub.forRoute(`/api/leases/${NEWER.lease_id}/transcript`, 'GET').length).toBeGreaterThan(0);
  });

  it('resets to the newest attempt when the selected chunk changes', async () => {
    const rendered = await render([OLDER(), NEWEST()]);
    stub = rendered.stub;
    const { el, fixture } = rendered;

    // Pick the older attempt of chunk A…
    el.querySelectorAll<HTMLElement>('[data-testid="attempt-tab"]')[0].click();
    await settle(fixture);

    // …then a different chunk is selected (its own two attempts).
    const OTHER_OLD = NEWEST({
      lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYOTHA',
      chunk_id: 'ch_OTHER',
      epoch: 1,
      session_id: 'sess-oth-old',
      state: 'closed',
      closure_reason: 'failed',
    });
    const OTHER_NEW = NEWEST({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYOTHB', chunk_id: 'ch_OTHER', session_id: 'sess-oth-new' });
    fixture.componentRef.setInput('leases', [OTHER_OLD, OTHER_NEW]);
    await settle(fixture);

    // The pick was scoped to chunk A, so the new chunk defaults to its newest.
    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    expect(tabs[1].getAttribute('aria-pressed')).toBe('true');
    expect(tabs[0].getAttribute('aria-pressed')).toBe('false');
    expect(stub.forRoute(`/api/leases/${OTHER_NEW.lease_id}/transcript`, 'GET').length).toBeGreaterThan(0);
  });
});
