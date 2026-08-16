import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { MachineDetail } from './chunk-detail';

/** A `ChunkDetailView` fixture — the runner's own `GET /api/chunks/{id}` pass-through
 * proxy response the header renders off. */
const HEADER = (overrides: Partial<runnerApi.ChunkDetailView> = {}): runnerApi.ChunkDetailView => ({
  chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
  graph_id: 'gr_1',
  current_node_id: 'nd_build',
  latest_epoch: 2,
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
function routes(header: runnerApi.ChunkDetailView = HEADER(), pauseResult: unknown = {}) {
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
  header: runnerApi.ChunkDetailView = HEADER(),
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
      // The header's chunk id links to the detail route now (issue #318).
      provideRouter([]),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(MachineDetail);
  fixture.componentRef.setInput('leases', leases);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture, stub };
}

/**
 * `MachineDetail`'s summary facts always render off the chunk's newest
 * attempt (the `leases` list's last entry) — per-attempt selection and the
 * transcript moved to the runner-local chunk detail route (issue #318,
 * `chunk-detail-page.spec.ts` covers that rendering contract now).
 */
describe('MachineDetail summary facts', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  it('shows SELECT A CHUNK when nothing is selected', async () => {
    const rendered = await render([]);
    stub = rendered.stub;
    const { el } = rendered;
    expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
  });

  it('shows facts for the newest attempt when the chunk has more than one', async () => {
    const rendered = await render([OLDER(), NEWEST()]);
    stub = rendered.stub;
    const { el } = rendered;

    const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
    expect(facts).toContain(NEW_LEASE);
    expect(facts).toContain('sess-new');
    expect(facts).not.toContain('sess-old');
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
    const rendered = await render([NEWEST()], header);
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
    const rendered = await render([NEWEST()], HEADER({ status: 'running', pause: null }));
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
    const rendered = await render([NEWEST()], HEADER({ status: 'running', pause: null }));
    stub = rendered.stub;
    const { el, fixture } = rendered;

    el.querySelector<HTMLElement>('[data-testid="pause-chunk"]')?.click();
    await settle(fixture);

    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(stub.forRoute(`/api/chunks/${NEWEST().chunk_id}/pause`, 'POST')).toHaveLength(0);
  });

  it('offers neither Pause nor Resume for a chunk in a non-pausable state', async () => {
    const rendered = await render([NEWEST()], HEADER({ status: 'done', pause: null }));
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="pause-chunk"]')).toBeNull();
    expect(el.querySelector('[data-testid="resume-chunk"]')).toBeNull();
  });
});

/**
 * The dock's panel chrome (issue #307) — it paints through `fleet-kit-panel`
 * now, the same chrome every sibling region in `local-panel-layout.ts` wears,
 * rather than mounting bare. `MachineDetailHeader` projects into the panel's
 * own `[header]` slot, so exactly one header bar (`KitPanel`'s own `.p-hdr`)
 * ever renders — never a second, stacked one, and never an empty one: `[hasHeaderContent]`
 * tracks whether a chunk is actually selected, so the no-selection rest state
 * renders no header bar at all rather than a bar with nothing in it.
 */
describe('MachineDetail panel chrome', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  it('renders inside a fleet-kit-panel with no header bar when nothing is selected', async () => {
    const empty = await render([]);
    stub = empty.stub;
    expect(empty.el.querySelector('[data-testid="machine-detail"]')?.tagName.toLowerCase()).toBe('fleet-kit-panel');
    expect(empty.el.querySelectorAll('.p-hdr')).toHaveLength(0);
  });

  it('renders inside a fleet-kit-panel with exactly one header bar, the header content inside it', async () => {
    const selected = await render([NEWEST()]);
    stub = selected.stub;
    expect(selected.el.querySelector('[data-testid="machine-detail"]')?.tagName.toLowerCase()).toBe('fleet-kit-panel');
    expect(selected.el.querySelectorAll('.p-hdr')).toHaveLength(1);
    // The header's own content lives inside that one bar, not stacked below it.
    expect(selected.el.querySelector('.p-hdr [data-testid="detail-chunk-ref"]')).not.toBeNull();
  });
});
