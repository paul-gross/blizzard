import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { vi } from 'vitest';

import { LocalIdentity } from './local-identity';
import { LocalPanelMobile } from './local-panel-mobile';
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
    imports: [LocalPanelMobile],
    providers: [
      provideZonelessChangeDetection(),
      // LocalPanelMobile now injects its own runner-status read (the titlebar's
      // `live` dot) plus renders `ChunkCard` and `LocalAsks`/`LocalInfo`, all of
      // whose own reads need a TanStack Query context to construct at all.
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(LocalPanelMobile);
  const defaults = {
    activeLeases: [LEASE()],
    leasesTriadState: 'ready',
    chunksTriadState: 'ready',
    machineChunks: [MACHINE_CHUNK],
    openAskCount: 0,
    ...overrides,
  };
  for (const [key, value] of Object.entries(defaults)) fixture.componentRef.setInput(key, value);
  await settle(fixture);
  return fixture;
}

describe('LocalPanelMobile', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    // Same reason as the provider above: only the self-contained children's own
    // reads need an answer, so every route resolves to the empty shape.
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
  });

  afterEach(() => stub.restore());

  it('stacks the four sections in attention order — info, agents, chunks, asks', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    const panes = ['mobile-info-pane', 'mobile-agents-pane', 'mobile-chunks-pane', 'mobile-asks-pane'].map(
      (testid) => el.querySelector(`[data-testid="${testid}"]`),
    );
    expect(panes.every((pane) => pane !== null)).toBe(true);

    const order = Array.from(el.querySelectorAll('[data-testid$="-pane"]')).map((node) =>
      node.getAttribute('data-testid'),
    );
    expect(order).toEqual(['mobile-info-pane', 'mobile-agents-pane', 'mobile-chunks-pane', 'mobile-asks-pane']);
  });

  it('renders the machine info section off its own query, no props needed', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-info"]')).not.toBeNull();
  });

  it('renders one agent row per active lease, with its heartbeat-freshness bar', async () => {
    const fixture = await render({ activeLeases: [LEASE()] });
    const el = fixture.nativeElement as HTMLElement;

    const rows = el.querySelectorAll('[data-testid="agent-row"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].querySelector('[data-testid="hb-freshness"]')).not.toBeNull();
  });

  it('renders the leases triad state it is handed, without a query of its own', async () => {
    const fixture = await render({ leasesTriadState: 'loading', activeLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="loading-state"]')).not.toBeNull();
  });

  it('renders one chunk card per machine chunk', async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid="local-chunk-card"]')).toHaveLength(1);
  });

  it('renders the chunks empty state when the machine holds no chunks', async () => {
    const fixture = await render({ chunksTriadState: 'empty', machineChunks: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="chunks-empty"]')?.textContent).toContain('NO CHUNKS ON THIS MACHINE');
  });

  it('renders the local asks section off its own query, count in the header note', async () => {
    const fixture = await render({ openAskCount: 3 });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="mobile-asks-pane"]')?.textContent).toContain('3 open');
    expect(el.querySelector('[data-testid="local-asks"]')).not.toBeNull();
  });

  it('shows the sections list, not a detail screen, while nothing is selected', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="panel-chunk-detail"]')).toBeNull();
    expect(el.querySelector('[data-testid="transcript-panel"]')).toBeNull();
    expect(el.querySelector('[data-testid="mobile-chunks-pane"]')).not.toBeNull();
  });

  it('emits the tapped chunk card id — the container writes the selection', async () => {
    const fixture = await render({ machineChunks: [MACHINE_CHUNK] });
    const el = fixture.nativeElement as HTMLElement;
    const picked: string[] = [];
    fixture.componentInstance.selectChunk.subscribe((id) => picked.push(id));

    el.querySelector<HTMLElement>('[data-testid="local-chunk-card"]')?.click();

    expect(picked).toEqual([LEASE().chunk_id]);
  });

  it('emits the tapped agent row lease id — a lease tap selects its chunk too', async () => {
    const fixture = await render({ activeLeases: [LEASE()] });
    const el = fixture.nativeElement as HTMLElement;
    const picked: string[] = [];
    fixture.componentInstance.selectLease.subscribe((id) => picked.push(id));

    el.querySelector<HTMLElement>('[data-testid="agent-row"]')?.click();

    expect(picked).toEqual([LEASE().lease_id]);
  });

  it('drills down to the chunk detail screen once the container resolves a selection', async () => {
    const fixture = await render({ selectedChunkLeases: [LEASE()], selectedStatus: { label: 'RUNNING', tone: 'running' } });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="panel-chunk-detail"]')).not.toBeNull();
    // The desktop dock reused verbatim — its execution facts and its transcript.
    const facts = el.querySelector('[data-testid="detail-facts"]')?.textContent ?? '';
    expect(facts).toContain('sess-77');
    expect(facts).toContain('4821');
    expect(facts).toContain('beta');
    expect(facts).toContain('/ws/beta');
    expect(el.querySelector('[data-testid="detail-transcript"]')).not.toBeNull();
    // …in place of the sections list, not beside it.
    expect(el.querySelector('[data-testid="mobile-chunks-pane"]')).toBeNull();
  });

  it('renders one attempt tab per lease of the selected chunk', async () => {
    const fixture = await render({
      selectedChunkLeases: [LEASE({ lease_id: 'lease_a1', epoch: 1, state: 'closed', closure_reason: 'transitioned' }), LEASE()],
      selectedAttemptLeaseId: LEASE().lease_id,
    });
    const el = fixture.nativeElement as HTMLElement;

    const tabs = Array.from(el.querySelectorAll('[data-testid="attempt-tab"]')).map((node) => node.textContent?.trim());
    expect(tabs).toEqual(['a1 transitioned', 'a2 running']);
  });

  it('emits selectAttempt when an attempt tab is picked', async () => {
    const fixture = await render({
      selectedChunkLeases: [LEASE({ lease_id: 'lease_a1', epoch: 1, state: 'closed', closure_reason: 'transitioned' }), LEASE()],
      selectedAttemptLeaseId: LEASE().lease_id,
    });
    const el = fixture.nativeElement as HTMLElement;
    const picked: string[] = [];
    fixture.componentInstance.selectAttempt.subscribe((id) => picked.push(id));

    el.querySelector<HTMLElement>('[data-testid="attempt-tab"]')?.click();

    expect(picked).toEqual(['lease_a1']);
  });

  it('emits closeDetail from the back affordance — the container clears the selection', async () => {
    const fixture = await render({ selectedChunkLeases: [LEASE()] });
    const el = fixture.nativeElement as HTMLElement;
    let closed = 0;
    fixture.componentInstance.closeDetail.subscribe(() => (closed += 1));

    el.querySelector<HTMLElement>('[data-testid="mobile-detail-back"]')?.click();

    expect(closed).toBe(1);
  });

  it('stays on the list when the selection names a chunk this machine does not hold', async () => {
    const fixture = await render({ selectedChunkLeases: [] });
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="panel-chunk-detail"]')).toBeNull();
    expect(el.querySelector('[data-testid="mobile-chunks-pane"]')).not.toBeNull();
  });

  it('renders the shared mobile titlebar with its own menu slot, closed by default', async () => {
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-panel-mobile-titlebar"]')).not.toBeNull();
    // The CDK renders the menu into an overlay on `document.body` (issue #161).
    expect(document.body.querySelector('[data-testid="local-panel-mobile-appearance"]')).toBeNull();

    el.querySelector<HTMLElement>('[data-testid="local-panel-mobile-titlebar-menu"]')?.click();
    await fixture.whenStable();

    expect(
      document.body.querySelector(
        '[data-testid="local-panel-mobile-titlebar-menu-panel"] [data-testid="local-panel-mobile-appearance"]',
      ),
    ).not.toBeNull();
  });

  /*
   * The titlebar menu is a real `role="menu"` since the CDK rebuild (issue #161),
   * so everything actionable inside it has to be a menu item: CDK's roving focus
   * only rovers `CdkMenuItem`s and `Tab` closes the menu rather than falling
   * through to a plain button, which would strand the identity block's own Log
   * out exactly where a mobile operator most needs it.
   */
  describe('the signed-in identity inside the titlebar menu', () => {
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
        .querySelector<HTMLElement>('[data-testid="local-panel-mobile-titlebar-menu"]')
        ?.click();
      await settle(fixture);
      return document.body.querySelector('[data-testid="local-panel-mobile-titlebar-menu-panel"]')!;
    };

    it('offers Log out as a real menu item the roving focus can reach', async () => {
      withSession({ auth_enabled: true, username: 'alice' });
      const panel = await openMenu(await render());

      const logout = panel.querySelector('[data-testid="local-panel-mobile-logout"]');
      expect(logout?.getAttribute('role')).toBe('menuitem');
      // In the tab order or one arrow key away — either way the key manager owns
      // it, which a plain <button> in here would never be.
      expect(logout?.getAttribute('tabindex')).not.toBeNull();
      // The identity block itself stays a non-focusable label: no second button.
      expect(panel.querySelector('[data-testid="identity-logout"]')).toBeNull();
      expect(panel.querySelector('[data-testid="identity-username"]')?.textContent).toContain('alice');
    });

    it('owns only menu items and presentational rows, per the role="menu" content model', async () => {
      withSession({ auth_enabled: true, username: 'alice' });
      const panel = await openMenu(await render());

      const allowed = ['menuitem', 'menuitemradio', 'menuitemcheckbox', 'group', 'separator', 'presentation'];
      const untyped = Array.from(panel.children).filter(
        (child) => !allowed.includes(child.getAttribute('role') ?? ''),
      );
      expect(untyped.map((child) => child.tagName)).toEqual([]);
    });

    it('logs out through that item', async () => {
      withSession({ auth_enabled: true, username: 'alice' });
      // Never actually navigate the jsdom window on logout — stubbed on the
      // prototype because the identity block lives inside the CDK overlay, out of
      // the fixture's own DebugElement tree.
      const reload = vi
        .spyOn(LocalIdentity.prototype as unknown as { reload: () => void }, 'reload')
        .mockImplementation(() => undefined);
      const fixture = await render();
      const panel = await openMenu(fixture);

      panel.querySelector<HTMLElement>('[data-testid="local-panel-mobile-logout"]')?.click();
      // Plain macrotask ticks rather than `settle`: triggering a menu item closes
      // the whole menu stack, so the overlay — and the identity block inside it —
      // is torn down while the logout POST is still in flight, and the fixture
      // never reports stable again. The request still goes out and the reload
      // still runs, which is what this asserts.
      for (let i = 0; i < 8; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));

      expect(stub.forRoute('/api/auth/logout', 'POST')).toHaveLength(1);
      expect(reload).toHaveBeenCalledTimes(1);
    });

    it('offers no Log out at all under a none-mode hub, where the surface is authless', async () => {
      withSession({ auth_enabled: false, username: null });
      const panel = await openMenu(await render());

      expect(panel.querySelector('[data-testid="local-panel-mobile-logout"]')).toBeNull();
      expect(panel.querySelector('[data-testid="local-identity"]')).toBeNull();
      // The appearance switcher is unconditional — it is not an auth concern.
      expect(panel.querySelector('[data-testid="local-panel-mobile-appearance"]')).not.toBeNull();
    });
  });

  it('derives the titlebar live dot from the runner status hub-reachable read', async () => {
    stub.restore();
    stub = stubRequestClient(runnerClient, (method, path) => {
      if (method === 'GET' && path === '/api/dashboard') {
        return {
          runner: {
            runner_id: 'runner-local',
            workspace_id: 'workspace-local',
            hub: {
              endpoint: 'http://127.0.0.1:8421',
              reachable: true,
              last_contact_at: null,
              buffer_depth: 0,
            },
            capacities: { used: 0, max_agents: 4, free: 4 },
            pause: { local: false, hub: false, effective: false },
            last_tick_at: null,
          },
          environments: { items: [] },
          asks: { items: [] },
          escalations: { items: [] },
          takeovers: { items: [] },
          facts: { items: [] },
          fleet_summary: null,
        };
      }
      return { items: [] };
    });
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(
      el.querySelector('[data-testid="local-panel-mobile-titlebar-livedot"]')?.classList.contains('active'),
    ).toBe(true);
  });
});
