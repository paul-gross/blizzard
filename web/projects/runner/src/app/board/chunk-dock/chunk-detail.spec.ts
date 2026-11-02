import { provideZonelessChangeDetection } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { chunkDetail, type RequestClientStub, settle, stubError, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import type { PanelLease } from '../../core/panel-reads.model';
import { MachineDetail } from './chunk-detail';

/** A `ChunkDetail` fixture — the runner's own `GET /api/chunks/{id}` pass-through
 * proxy response the header renders off. */
const HEADER = (overrides: Partial<runnerApi.ChunkDetail> = {}): runnerApi.ChunkDetail =>
  chunkDetail({
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    graph_id: 'gr_1',
    current_node_id: 'nd_build',
    latest_epoch: 2,
    status: 'running',
    pausable: true,
    work_refs: [{ source: 'blizzard', ref: '185', label: 'blizzard#185', web_url: 'https://forge.example/issues/185' }],
    pause: null,
    ...overrides,
  });

const NEW_LEASE = 'lease_01KXKVVF1J3D6H6VYZ3XYNNEW1';
const OLD_LEASE = 'lease_01KXKVVF1J3D6H6VYZ3XYNOLD1';

/** A running newest attempt (epoch 2) — the chunk's freshest lease, read off runner `rn_1`. */
const NEWEST = (overrides: Partial<PanelLease> = {}): PanelLease => ({
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
  display_status: 'running',
  closure_healthy: false,
  closed_at: null,
  closure_reason: null,
  stale_after_seconds: 3600,
  resolved_model: null,
  resolved_effort: null,
  harness_version: null,
  git_commits: [],
  harness_id: null,
  project_id: null,
  owner: { runnerId: 'rn_1', name: 'r1', label: 'r1' },
  projectSlug: 'p',
  ...overrides,
});

/** An older, failed attempt (epoch 1) of the same chunk. */
const OLDER = (overrides: Partial<PanelLease> = {}): PanelLease =>
  NEWEST({
    lease_id: OLD_LEASE,
    epoch: 1,
    session_id: 'sess-old',
    state: 'closed',
    closed_at: '2026-07-16T10:30:00.000Z',
    closure_reason: 'failed',
    ...overrides,
  });

/** The host's one runner, binding project `p`. */
const HOST_RUNNER = {
  runner_id: 'rn_1',
  name: 'r1',
  state: 'serving',
  served: true,
  halted_reasons: [],
  pause: { local: false, hub: false, effective: false, local_reason: null },
  bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }],
  agents_in_use: 1,
  max_agents: 4,
};

/** `rn_1`'s dashboard, naming the project it serves under `p`. */
const PROJECT_DASHBOARD = {
  runner: { served_projects: [{ entry: 'p', project_id: 'proj_p', slug: 'p', name: 'Project P', color: null }] },
};

/** A transcript route that answers every lease's read with an empty transcript. */
const TRANSCRIPT_ROUTE = /^\/api\/leases\/([^/]+)\/transcript$/;
const CHUNK_ROUTE = /^\/runners\/rn_1\/api\/chunks\/([^/]+)$/;
const PAUSE_ROUTE = /^\/runners\/rn_1\/api\/chunks\/([^/]+)\/pause$/;
const RESUME_ROUTE = /^\/runners\/rn_1\/api\/chunks\/([^/]+)\/resume$/;

/** The header's own severable read + mutation routes, layered onto the transcript
 * route every test needs — `header` answers `GET /api/chunks/{id}`, `pauseResult`
 * answers both `POST .../pause` and `POST .../resume` (a `ChunkSummary`, or a
 * {@link stubError} to exercise the 409 refusal). */
function routes(header: runnerApi.ChunkDetail = HEADER(), pauseResult: unknown = {}) {
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
  leases: readonly PanelLease[],
  header: runnerApi.ChunkDetail = HEADER(),
  pauseResult: unknown = {},
): Promise<{
  el: HTMLElement;
  fixture: ComponentFixture<MachineDetail>;
  stub: RequestClientStub;
}> {
  const stub = stubRequestClient(runnerClient, routes(header, pauseResult));
  return { ...(await mount(leases)), stub };
}

/** Mounts the dock over `leases` against whatever stub is already installed. */
async function mount(leases: readonly PanelLease[]): Promise<{ el: HTMLElement; fixture: ComponentFixture<MachineDetail> }> {
  await TestBed.configureTestingModule({
    imports: [MachineDetail],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      // The header's chunk id is a `routerLink`.
      provideRouter([]),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(MachineDetail);
  fixture.componentRef.setInput('leases', leases);
  await settle(fixture);
  return { el: fixture.nativeElement as HTMLElement, fixture };
}

/** Opens the header's ⋯ menu and picks the action `testid` names — the CDK renders the menu
 * into an overlay on `document.body`, not inside the fixture. */
async function clickMenuAction(fixture: ComponentFixture<MachineDetail>, testid: string): Promise<void> {
  (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="chunk-actions-menu"]')?.click();
  await fixture.whenStable();
  document.body.querySelector<HTMLElement>(`[data-testid="${testid}"]`)?.click();
  await fixture.whenStable();
}

/** The menu action `testid`'s `aria-disabled`, read with the menu opened and closed again. */
async function actionDisabled(fixture: ComponentFixture<MachineDetail>, testid: string): Promise<string | null | undefined> {
  const trigger = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="chunk-actions-menu"]');
  trigger?.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  fixture.detectChanges();
  const value = document.body.querySelector(`[data-testid="${testid}"]`)?.getAttribute('aria-disabled');
  trigger?.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  fixture.detectChanges();
  return value;
}

/** The menu actions on offer, read with the menu opened and closed again. */
async function menuActions(fixture: ComponentFixture<MachineDetail>): Promise<string[]> {
  const trigger = (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="chunk-actions-menu"]');
  if (!trigger) return [];
  trigger.click();
  await fixture.whenStable();
  const actions = ['pause-chunk', 'resume-chunk'].filter((testid) => document.body.querySelector(`[data-testid="${testid}"]`) !== null);
  trigger.click();
  await fixture.whenStable();
  return actions;
}

/**
 * `MachineDetail`'s summary facts always render off the chunk's newest
 * attempt (the `leases` list's last entry).
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
    expect(facts).toContain('sess-new');
    expect(facts).toContain('4821');
    expect(facts).not.toContain('sess-old');
  });
});

/**
 * The header: pause/resume and work-item links, driven through the stubbed
 * `GET /api/chunks/{id}` route behind {@link injectChunkDetailQuery}.
 */
describe('MachineDetail header', () => {
  let stub: RequestClientStub;

  afterEach(() => {
    stub.restore();
  });

  it('shows the full chunk id, not the compact shortname or a "chunk detail" label', async () => {
    const rendered = await render([NEWEST()]);
    stub = rendered.stub;
    const { el } = rendered;

    expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toBe(NEWEST().chunk_id);
    expect(el.textContent).not.toContain('chunk detail');
  });

  it('links the chunk id to the chunk page, keeping the filters', async () => {
    const rendered = await render([NEWEST()]);
    stub = rendered.stub;

    const ref = rendered.el.querySelector('[data-testid="detail-chunk-ref"]');
    expect(ref?.getAttribute('href')).toBe(`/board/chunk/${NEWEST().chunk_id}`);
  });

  it("names the chunk's runner and its project by name", async () => {
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === '/api/host/runners') return { items: [HOST_RUNNER] };
      if (method === 'GET' && path === '/runners/rn_1/api/dashboard') return PROJECT_DASHBOARD;
      return routes()(method, path);
    });
    const { el } = await mount([NEWEST()]);

    expect(el.querySelector('[data-testid="detail-runner"]')?.textContent?.trim()).toBe('r1');
    expect(el.querySelector('[data-testid="detail-project"]')?.textContent?.trim()).toBe('Project P');
  });

  it("reads and acts on the newest attempt's own runner", async () => {
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === `/runners/rn_2/api/chunks/${NEWEST().chunk_id}`) return HEADER();
      if (method === 'POST' && path === `/runners/rn_2/api/chunks/${NEWEST().chunk_id}/pause`) return {};
      return {};
    });
    const { el, fixture } = await mount([NEWEST({ owner: { runnerId: 'rn_2', name: 'r2', label: 'r2' } })]);

    expect(el.querySelector('[data-testid="detail-pointer"]')?.textContent).toContain('blizzard#185');
    await clickMenuAction(fixture, 'pause-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    expect(stub.forRoute(`/runners/rn_2/api/chunks/${NEWEST().chunk_id}/pause`, 'POST')).toHaveLength(1);
    expect(stub.forRoute(`/runners/rn_1/api/chunks/${NEWEST().chunk_id}/pause`, 'POST')).toHaveLength(0);
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

    const close = el.querySelector<HTMLButtonElement>('[data-testid="detail-close"]');
    expect(close?.tagName).toBe('BUTTON');
    expect(close?.getAttribute('aria-label')).toBe('Close');
    close?.click();

    expect(dismissed).toBe(true);
  });

  it('offers no detach button', async () => {
    const rendered = await render([NEWEST()]);
    stub = rendered.stub;
    expect(rendered.el.querySelector('[data-testid="detach-chunk"]')).toBeNull();
  });

  it('offers Pause for an unpaused, pausable chunk and fires the mutation once confirmed', async () => {
    const rendered = await render([NEWEST()], HEADER({ status: 'running', pause: null }));
    stub = rendered.stub;
    const { el, fixture } = rendered;

    expect(await menuActions(fixture)).toEqual(['pause-chunk']);
    await clickMenuAction(fixture, 'pause-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    expect(stub.forRoute(`/runners/rn_1/api/chunks/${NEWEST().chunk_id}/pause`, 'POST')).toHaveLength(1);
  });

  it('offers Resume for a paused chunk and fires the mutation once confirmed', async () => {
    const rendered = await render(
      [NEWEST()],
      HEADER({ status: 'paused', pause: { by: 'operator', set_at: '2026-07-16T11:00:00.000Z' } }),
    );
    stub = rendered.stub;
    const { el, fixture } = rendered;

    expect(await menuActions(fixture)).toEqual(['resume-chunk']);
    await clickMenuAction(fixture, 'resume-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    expect(stub.forRoute(`/runners/rn_1/api/chunks/${NEWEST().chunk_id}/resume`, 'POST')).toHaveLength(1);
  });

  it('does not fire the pause mutation when the operator declines the confirm', async () => {
    const rendered = await render([NEWEST()], HEADER({ status: 'running', pause: null }));
    stub = rendered.stub;
    const { el, fixture } = rendered;

    await clickMenuAction(fixture, 'pause-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-cancel"]')?.click();
    await settle(fixture);

    expect(stub.forRoute(`/runners/rn_1/api/chunks/${NEWEST().chunk_id}/pause`, 'POST')).toHaveLength(0);
  });

  it('offers neither Pause nor Resume for a chunk in a non-pausable state', async () => {
    const rendered = await render([NEWEST()], HEADER({ status: 'done', pausable: false, pause: null }));
    stub = rendered.stub;
    const { el, fixture } = rendered;

    expect(el.querySelector('[data-testid="chunk-actions-menu"]')).toBeNull();
    expect(await menuActions(fixture)).toEqual([]);
  });
});

/**
 * Pause/Resume's own pending-disable and failure reporting: the button that fired the
 * mutation disables for its duration and re-enables once it settles, and a rejected
 * mutation renders inline rather than being swallowed. Every "held pending" assertion
 * spies on `queryClient.invalidateQueries` and returns a promise it controls rather than
 * letting the stub's fetch settle on its own, because `injectChunkPauseMutation`'s
 * `onSettled` keeps `isPending()` true only until its own invalidations resolve.
 */
describe('MachineDetail Pause/Resume pending + failure', () => {
  let stub: RequestClientStub;

  afterEach(() => stub.restore());

  it('disables Pause while the mutation is pending, re-enabling once it settles', async () => {
    const rendered = await render([NEWEST()], HEADER({ status: 'running', pause: null }));
    stub = rendered.stub;
    const { el, fixture } = rendered;
    const queryClient = TestBed.inject(QueryClient);
    let resolveInvalidate!: () => void;
    vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
      new Promise<void>((resolve) => (resolveInvalidate = resolve)),
    );

    await clickMenuAction(fixture, 'pause-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    // Held open by the `invalidateQueries` spy above — `settle()`'s own `whenStable()`
    // would hang on it, so a bare macrotask tick + a manual `detectChanges()` stands in.
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    expect(await actionDisabled(fixture, 'pause-chunk')).toBe('true');

    resolveInvalidate();
    await settle(fixture);

    expect(await actionDisabled(fixture, 'pause-chunk')).not.toBe('true');
  });

  it('disables Resume while the mutation is pending, re-enabling once it settles', async () => {
    const rendered = await render(
      [NEWEST()],
      HEADER({ status: 'paused', pause: { by: 'operator', set_at: '2026-07-16T11:00:00.000Z' } }),
    );
    stub = rendered.stub;
    const { el, fixture } = rendered;
    const queryClient = TestBed.inject(QueryClient);
    let resolveInvalidate!: () => void;
    vi.spyOn(queryClient, 'invalidateQueries').mockReturnValue(
      new Promise<void>((resolve) => (resolveInvalidate = resolve)),
    );

    await clickMenuAction(fixture, 'resume-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();

    expect(await actionDisabled(fixture, 'resume-chunk')).toBe('true');

    resolveInvalidate();
    await settle(fixture);

    expect(await actionDisabled(fixture, 'resume-chunk')).not.toBe('true');
  });

  it('surfaces a rejected Pause inline rather than swallowing it', async () => {
    const rendered = await render(
      [NEWEST()],
      HEADER({ status: 'running', pause: null }),
      stubError(409, { detail: 'chunk is already paused' }),
    );
    stub = rendered.stub;
    const { el, fixture } = rendered;

    await clickMenuAction(fixture, 'pause-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('already paused');
    // A rejected flip is not a stuck control — the button re-enables.
    expect(await actionDisabled(fixture, 'pause-chunk')).not.toBe('true');
  });

  it('surfaces a rejected Resume inline rather than swallowing it', async () => {
    const rendered = await render(
      [NEWEST()],
      HEADER({ status: 'paused', pause: { by: 'operator', set_at: '2026-07-16T11:00:00.000Z' } }),
      stubError(409, { detail: 'chunk is not paused' }),
    );
    stub = rendered.stub;
    const { el, fixture } = rendered;

    await clickMenuAction(fixture, 'resume-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="action-error"]')?.textContent).toContain('chunk is not paused');
  });

  it('clears a stale action error on the next attempt', async () => {
    const rendered = await render(
      [NEWEST()],
      HEADER({ status: 'running', pause: null }),
      stubError(500, { detail: 'runner store unwired' }),
    );
    stub = rendered.stub;
    const { el, fixture } = rendered;

    await clickMenuAction(fixture, 'pause-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);
    expect(el.querySelector('[data-testid="action-error"]')).not.toBeNull();

    await clickMenuAction(fixture, 'pause-chunk');
    el.querySelector<HTMLElement>('[data-testid="confirm-dialog-confirm"]')?.click();
    await settle(fixture);

    expect(stub.forRoute(`/runners/rn_1/api/chunks/${NEWEST().chunk_id}/pause`, 'POST')).toHaveLength(2);
    expect(el.querySelector('[data-testid="action-error"]')).not.toBeNull();
  });
});

/**
 * The dock's panel chrome — it paints through `fleet-kit-panel`.
 * `MachineDetailHeader` projects into the panel's
 * own `fleetKitPanelHeader` slot, so exactly one header bar (`KitPanel`'s own `.p-hdr`)
 * ever renders — never a second, stacked one, and never an empty one: the panel
 * observes its own slot occupancy, so the no-selection rest state (which projects
 * nothing) renders no header bar at all rather than a bar with nothing in it.
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

/**
 * The heartbeat label reads off {@link injectNowSignal}, not a bare `Date.now()`
 * inside a `computed()` (`bzh:frontend-formatters`) — so it advances on the tick
 * alone, the same contract `HeartbeatFreshness`'s own bar honors beside it.
 */
describe('MachineDetail heartbeat label ticking', () => {
  it('advances the heartbeat label on the tick alone, with the leases input unchanged', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse(NEWEST().last_heartbeat_at!));
    let stub: RequestClientStub | undefined;
    try {
      stub = stubRequestClient(runnerClient, routes());
      await TestBed.configureTestingModule({
        imports: [MachineDetail],
        providers: [
          provideZonelessChangeDetection(),
              provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
          provideRouter([]),
        ],
      }).compileComponents();
      const fixture = TestBed.createComponent(MachineDetail);
      fixture.componentRef.setInput('leases', [NEWEST()]);
      fixture.detectChanges();
      await vi.advanceTimersByTimeAsync(0);
      const el = fixture.nativeElement as HTMLElement;

      expect(el.querySelector('[data-testid="heartbeat-label"]')?.textContent).toContain('-0s');

      // The tick alone — the leases input never changes — advances the label.
      await vi.advanceTimersByTimeAsync(5_000);
      fixture.detectChanges();

      expect(el.querySelector('[data-testid="heartbeat-label"]')?.textContent).toContain('-5s');
    } finally {
      stub?.restore();
      vi.useRealTimers();
    }
  });
});

describe('MachineDetail Requeue', () => {
  let stub: RequestClientStub;
  afterEach(() => stub.restore());

  const ESCALATION: runnerApi.EscalationView = {
    chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
    lease_id: NEW_LEASE,
    node_id: 'nd_build',
    epoch: 2,
    closed_at: '2026-07-16T11:00:00.000Z',
    resume_command: 'blizzard runner resume sess-new',
    wrapped_takeover_command: null,
    closure: null,
    cause: null,
    effort: null,
    harness_id: null,
    harness_version: null,
    model: null,
    project_id: null,
    session_name: null,
  };
  const REQUEUE_ROUTE = '/runners/rn_2/api/chunks/ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9/requeues';

  /** The dock over an escalated chunk owned by `rn_2`, the requeue POST answered by `result`. */
  async function mountEscalated(result: unknown) {
    stub = stubRequestClient(runnerClient, (method, path) => (method === 'POST' && path === REQUEUE_ROUTE ? result : {}));
    await TestBed.configureTestingModule({
      imports: [MachineDetail],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([]),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(MachineDetail);
    fixture.componentRef.setInput('leases', [NEWEST({ owner: { runnerId: 'rn_2', name: 'r2', label: 'r2' } })]);
    fixture.componentRef.setInput('escalation', ESCALATION);
    await settle(fixture);
    const el = fixture.nativeElement as HTMLElement;
    el.querySelector<HTMLButtonElement>('button[data-testid="requeue-chunk"]')!.click();
    await settle(fixture);
    return { fixture, el };
  }

  it('POSTs to the owning runner’s requeue route once confirmed, then shows the requeued notice', async () => {
    const { fixture, el } = await mountEscalated({ chunk_id: ESCALATION.chunk_id, requeued: true });
    expect(stub.forRoute(REQUEUE_ROUTE, 'POST')).toHaveLength(0);

    el.querySelector<HTMLElement>('[data-testid="confirm-requeue-confirm"]')!.click();
    await settle(fixture);

    expect(stub.forRoute(REQUEUE_ROUTE, 'POST')).toHaveLength(1);
    expect(el.querySelector('[data-testid="requeued-notice"]')?.textContent).toContain('a fresh attempt spawns at its current node');
  });

  it('sends nothing when the operator cancels', async () => {
    const { fixture, el } = await mountEscalated({});

    el.querySelector<HTMLElement>('[data-testid="confirm-requeue-cancel"]')!.click();
    await settle(fixture);

    expect(stub.forRoute(REQUEUE_ROUTE, 'POST')).toHaveLength(0);
    expect(el.querySelector('[data-testid="confirm-requeue"]')).toBeNull();
  });

  it('renders a refused requeue on the error line', async () => {
    const { fixture, el } = await mountEscalated(stubError(409, { detail: 'end the interactive session first' }));

    el.querySelector<HTMLElement>('[data-testid="confirm-requeue-confirm"]')!.click();
    await settle(fixture);

    expect(el.querySelector('[data-testid="requeue-error"]')?.textContent).toContain('end the interactive session first');
    expect(el.querySelector('[data-testid="requeued-notice"]')).toBeNull();
    expect(el.querySelector<HTMLButtonElement>('button[data-testid="requeue-chunk"]')!.disabled).toBe(false);
  });
});
