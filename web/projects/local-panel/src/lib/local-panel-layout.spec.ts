import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, hiddenAtContainerWidth, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { LocalIdentity } from './local-identity';
import { LocalPanelLayout } from './local-panel-layout';
import type { MachineChunkRow } from './local-panel';

const LEASE = (overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView => ({
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

const MACHINE_CHUNK: MachineChunkRow = { lease: LEASE(), leases: [LEASE()], status: { label: 'RUNNING', tone: 'running' } };

async function render(overrides: Record<string, unknown> = {}) {
  await TestBed.configureTestingModule({
    imports: [LocalPanelLayout],
    providers: [
      provideZonelessChangeDetection(),
      // `LocalPanelLayout` itself is presentational, but it composes several
      // self-fetching mini-containers that inject their own queries/mutations —
      // `ChunkRow`'s severable work-item-title read (issue #28, decision 1),
      // `LocalPauseControl` (issue #133), and `LocalIdentity` — so a TanStack
      // Query context has to exist for the fixture to construct at all.
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPanelLayout);
  const defaults = {
    connection: 'ok',
    headerStats: [
      { key: 'envs', label: 'Envs', value: 2, capacity: 4 },
      { key: 'agents', label: 'Agents', value: 1, capacity: 2 },
    ],
    activeLeases: [LEASE()],
    leasesTriadState: 'ready',
    chunksTriadState: 'ready',
    chunksEmptyText: 'NO CHUNKS ON THIS MACHINE',
    machineChunks: [MACHINE_CHUNK],
    showAllChunks: false,
    openAskCount: 0,
    selectedChunkId: null,
    selectedChunkLeases: [],
    selectedAttemptLeaseId: null,
    selectedStatus: null,
    selectedEscalation: null,
    ...overrides,
  };
  for (const [key, value] of Object.entries(defaults)) fixture.componentRef.setInput(key, value);
  await fixture.whenStable();
  return fixture;
}

describe('LocalPanelLayout', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    // Same reason as the provider above: every mini-container's own read (`ChunkRow`'s
    // work-item-title lookup, `LocalPauseControl`'s and `LocalIdentity`'s status reads) just
    // needs an answer, not a realistic one, so every route resolves to this one empty
    // shape. `GET /api/runner` carries no `pause` here, so `LocalPauseControl` reads
    // both brakes off and renders `Pause` in every spec in this file — harmless for
    // what this file asserts, but worth knowing if a future spec here starts caring.
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
  });

  afterEach(() => stub.restore());

  it('reflects the connection input in the header — off plain inputs alone', async () => {
    const fixture = await render({ connection: 'ok' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('ok');
  });

  it('shows offline in the header when the connection input says so', async () => {
    const fixture = await render({ connection: 'offline' });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('offline');
  });

  it('renders the header stat cells it is handed, off plain inputs alone (issue #131)', async () => {
    const fixture = await render({
      headerStats: [
        { key: 'envs', label: 'Envs', value: 2, capacity: 4 },
        { key: 'agents', label: 'Agents', value: 1, capacity: 2 },
      ],
    });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="stat-envs"]')?.textContent?.trim()).toBe('2/4');
    expect(el.querySelector('[data-testid="stat-agents"]')?.textContent?.trim()).toBe('1/2');
  });

  it('renders the shared 48px board header, not a bespoke local one (issue #131)', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-header"]')).not.toBeNull();
    expect(el.querySelector('.lp-header')).toBeNull();
  });

  it('renders the async triad state it is handed, without a query of its own', async () => {
    const fixture = await render({ leasesTriadState: 'loading', activeLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="loading-state"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="empty-state"]')).toBeNull();
  });

  it('renders one agent-row per active lease', async () => {
    const fixture = await render({ activeLeases: [LEASE()] });
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="agent-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].getAttribute('data-lease-id')).toBe('lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
  });

  it('emits selectLease when an agent row is activated', async () => {
    const fixture = await render({ activeLeases: [LEASE()] });
    let selected: string | undefined;
    fixture.componentInstance.selectLease.subscribe((id) => (selected = id));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="agent-row"]')?.click();
    expect(selected).toBe('lease_01KXKVVF1J3D6H6VYZ3XYNZPRR');
  });

  it('renders one chunk-row per machine chunk and marks the selected one', async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK], selectedChunkId: MACHINE_CHUNK.lease.chunk_id });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="chunk-row"]')).toHaveLength(1);
    expect(el.querySelector('[data-testid="chunk-row"]')?.classList.contains('selected')).toBe(true);
  });

  it("wires the chunk row's lease/status inputs through to ChunkRow — content itself is ChunkRow's own spec (issue #134)", async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK] });
    const el = fixture.nativeElement as HTMLElement;
    const card = el.querySelector<HTMLElement>('[data-testid="chunk-row"]');

    expect(card?.getAttribute('data-chunk-id')).toBe(MACHINE_CHUNK.lease.chunk_id);
    // The lane-colored left edge — proof the `status` input itself (not just
    // the chunk id) reached the child, the derived status's own tone color.
    expect(card?.style.borderLeftColor).toBe('var(--amber)');
  });

  it('renders the "show all" checkbox bar above the chunks list, reflecting the input and emitting on toggle', async () => {
    const fixture = await render({ showAllChunks: false });
    let toggled: boolean | undefined;
    fixture.componentInstance.toggleShowAllChunks.subscribe((checked) => (toggled = checked));
    const el = fixture.nativeElement as HTMLElement;
    const checkbox = el.querySelector<HTMLInputElement>('[data-testid="chunk-filter-show-all"]');

    expect(checkbox?.checked).toBe(false);

    checkbox?.click();
    expect(toggled).toBe(true);
  });

  it('reflects a checked "show all" input and emits false on uncheck', async () => {
    const fixture = await render({ showAllChunks: true });
    let toggled: boolean | undefined;
    fixture.componentInstance.toggleShowAllChunks.subscribe((checked) => (toggled = checked));
    const el = fixture.nativeElement as HTMLElement;
    const checkbox = el.querySelector<HTMLInputElement>('[data-testid="chunk-filter-show-all"]');

    expect(checkbox?.checked).toBe(true);

    checkbox?.click();
    expect(toggled).toBe(false);
  });

  it('emits selectChunk when a chunk row is activated', async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK] });
    let selected: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((id) => (selected = id));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="chunk-row"]')?.click();
    expect(selected).toBe(MACHINE_CHUNK.lease.chunk_id);
  });

  it('shows the SELECT A CHUNK placeholder in the detail dock before anything is selected', async () => {
    const fixture = await render({ selectedChunkLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-empty"]')?.textContent).toContain('SELECT A CHUNK');
  });

  it('renders the selected chunk in the detail dock, summary off the newest attempt', async () => {
    const fixture = await render({ selectedChunkLeases: [LEASE()], selectedStatus: MACHINE_CHUNK.status });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="detail-chunk-ref"]')?.textContent).toContain('C-3YJ9');
  });

  it('marks the attempt tab named by selectedAttemptLeaseId as active in the detail dock', async () => {
    const older = LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNOLD1', epoch: 1, state: 'closed', closure_reason: 'failed' });
    const fixture = await render({
      selectedChunkLeases: [older, LEASE()],
      selectedStatus: MACHINE_CHUNK.status,
      selectedAttemptLeaseId: older.lease_id,
    });
    const el = fixture.nativeElement as HTMLElement;

    const tabs = el.querySelectorAll('[data-testid="attempt-tab"]');
    // The older attempt (index 0) is the one selectedAttemptLeaseId names.
    expect(tabs[0].getAttribute('aria-pressed')).toBe('true');
    expect(tabs[1].getAttribute('aria-pressed')).toBe('false');
  });

  it('re-emits selectAttempt when an attempt tab is picked in the detail dock', async () => {
    const older = LEASE({ lease_id: 'lease_01KXKVVF1J3D6H6VYZ3XYNOLD1', epoch: 1, state: 'closed', closure_reason: 'failed' });
    const fixture = await render({
      selectedChunkLeases: [older, LEASE()],
      selectedStatus: MACHINE_CHUNK.status,
      selectedAttemptLeaseId: LEASE().lease_id,
    });
    let picked: string | undefined;
    fixture.componentInstance.selectAttempt.subscribe((id) => (picked = id));
    const el = fixture.nativeElement as HTMLElement;

    el.querySelectorAll<HTMLElement>('[data-testid="attempt-tab"]')[0].click();
    expect(picked).toBe(older.lease_id);
  });

  it('buries the appearance switcher behind the header menu, closed by default', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    // The CDK renders the menu into an overlay on `document.body` (issue #161),
    // outside the fixture's own element.
    expect(document.body.querySelector('[data-testid="local-panel-appearance"]')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="local-panel-menu"]')?.click();
    await fixture.whenStable();

    expect(
      document.body.querySelector('[data-testid="local-panel-menu-panel"] [data-testid="local-panel-appearance"]'),
    ).not.toBeNull();

    document.body.querySelector<HTMLElement>('[data-testid="local-panel-appearance"]')?.click();
    await fixture.whenStable();

    expect(
      document.body.querySelector('[data-testid="local-panel-appearance-panel"] [data-testid="viewport-menu-auto"]'),
    ).not.toBeNull();
  });

  /*
   * The desktop profile menu owns this shell's logout since #163's narrow tier
   * hides the identity block outright — a header cell that disappears must not
   * take the only way out with it. The default stub in this file answers the
   * session route with the same empty shape as everything else, so these two
   * re-stub it with a real signed-in session.
   */
  describe('Log out in the profile menu', () => {
    const withSession = (session: unknown) => {
      stub.restore();
      stub = stubRequestClient(runnerClient, (method, path) => {
        if (method === 'GET' && path === '/api/auth/session') return session;
        if (method === 'POST' && path === '/api/auth/logout') return {};
        return { items: [] };
      });
    };

    const openMenu = async (fixture: Awaited<ReturnType<typeof render>>) => {
      (fixture.nativeElement as HTMLElement)
        .querySelector<HTMLElement>('[data-testid="local-panel-menu"]')
        ?.click();
      await settle(fixture);
      return document.body.querySelector('[data-testid="local-panel-menu-panel"]')!;
    };

    it('carries Log out as a menu item, so it survives the header cell collapsing', async () => {
      withSession({ auth_enabled: true, username: 'alice' });
      const fixture = await render();

      // Display-only in the header: exactly one logout affordance per shell, and
      // all three profile menus now carry the same two items.
      expect((fixture.nativeElement as HTMLElement).querySelector('[data-testid="identity-logout"]')).toBeNull();

      const panel = await openMenu(fixture);
      const items = Array.from(panel.querySelectorAll('[role="menuitem"]')).map((i) =>
        i.getAttribute('data-testid'),
      );
      expect(items).toEqual(['local-panel-logout', 'local-panel-appearance']);
    });

    it('logs out through that item', async () => {
      withSession({ auth_enabled: true, username: 'alice' });
      const reload = vi
        .spyOn(LocalIdentity.prototype as unknown as { reload: () => void }, 'reload')
        .mockImplementation(() => undefined);
      const fixture = await render();
      const panel = await openMenu(fixture);

      panel.querySelector<HTMLElement>('[data-testid="local-panel-logout"]')?.click();
      // Triggering an item tears the overlay down mid-flight, so the fixture
      // never restabilizes — plain macrotask ticks rather than `settle`.
      for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));

      expect(stub.forRoute('/api/auth/logout', 'POST')).toHaveLength(1);
      expect(reload).toHaveBeenCalledTimes(1);
    });

    it('offers no Log out under a none-mode hub, where the surface is authless', async () => {
      withSession({ auth_enabled: false, username: null });
      const panel = await openMenu(await render());

      expect(panel.querySelector('[data-testid="local-panel-logout"]')).toBeNull();
      expect(panel.querySelector('[data-testid="local-panel-appearance"]')).not.toBeNull();
    });
  });

  it('renders the shared avatar-circle trigger on the header menu (issue #132)', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-panel-menu"] fleet-kit-avatar')).not.toBeNull();
  });

  /*
   * This shell's half of the header's tiered collapse (issue #163). `BoardHeader`
   * pins its trailing cluster `flex: none`, but it can only collapse the cells it
   * renders itself — and this shell projects a pause control and an identity
   * block in beside the menu, where the hub board projects only the menu. Left
   * standing they push the profile menu clean off a phone-width header, and in
   * desktop mode that menu is this shell's ONLY appearance switcher, so a phone
   * pinned to desktop would be stranded there.
   *
   * jsdom parses `@container` rules without evaluating them, so these resolve the
   * rules this component actually ships at a given header width rather than
   * trusting `getComputedStyle`, which reports the wide-tier value at every one.
   */
  describe('trailing-cluster collapse at narrow header widths (issue #163)', () => {
    const at = (el: HTMLElement, selector: string, width: number) =>
      hiddenAtContainerWidth(el.querySelector(selector)!, { containerName: 'board-header', width });

    it('keeps every trailing control at a full-width header', async () => {
      const el = (await render()).nativeElement as HTMLElement;

      expect(at(el, 'local-pause-control', 1400)).toBe(false);
      expect(at(el, 'local-identity', 1400)).toBe(false);
      expect(at(el, '[data-testid="local-panel-menu"]', 1400)).toBe(false);
    });

    it('gives up the pause control and identity at the narrow tier, never the profile menu', async () => {
      const el = (await render()).nativeElement as HTMLElement;

      expect(at(el, 'local-pause-control', 390)).toBe(true);
      expect(at(el, 'local-identity', 390)).toBe(true);
      // The one that must survive: it is the only way back to mobile from a
      // forced-desktop phone on this shell.
      expect(at(el, '[data-testid="local-panel-menu"]', 390)).toBe(false);
    });

    it('steers the cluster\'s shrink into the username, never into the menu', async () => {
      // The breakpoint above handles phone widths; this handles the band just
      // above it, where a long enough username would otherwise push the menu off
      // at *any* width. jsdom does no flex layout, so this pins the declarations
      // that decide where the shrink lands — the widths are proven in a browser.
      const el = (await render()).nativeElement as HTMLElement;

      expect(getComputedStyle(el.querySelector<HTMLElement>('local-identity')!).minWidth).toBe('0px');
      expect(getComputedStyle(el.querySelector<HTMLElement>('.menu')!).flexShrink).toBe('0');
      expect(getComputedStyle(el.querySelector<HTMLElement>('local-pause-control')!).flexShrink).toBe('0');
    });

    it('rides the same named container the header declares, not a viewport media query', async () => {
      const el = (await render()).nativeElement as HTMLElement;

      // Styling nodes it declared itself, against `BoardHeader`'s named query
      // container — the shell reacts to the *header's* width, so it collapses on
      // its own terms rather than the window's.
      expect(getComputedStyle(el.querySelector('.mc-header')!).containerName).toBe('board-header');
      expect(at(el, 'local-pause-control', 700)).toBe(false);
      expect(at(el, 'local-pause-control', 699)).toBe(true);
    });
  });
});
