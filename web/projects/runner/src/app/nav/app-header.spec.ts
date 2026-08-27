import { type Provider, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient, type runnerApi, type SseStatus } from 'fleet';
import { type RequestClientStub, hiddenAtContainerWidth, settle, stubError, stubRequestClient } from 'fleet/testing';
import { LocalIdentity, RunnerLiveUpdates } from 'local-panel';
import { vi } from 'vitest';

import { AppHeader } from './app-header';

/** A `RunnerLiveUpdates` stand-in so a spec can drive {@link AppHeader}'s
 * `connection` fold (blizzard#333) without opening a real stream — the header
 * only ever reads `status`/`authFailed`, never starts or restarts it. */
function fakeLiveUpdates(status: SseStatus, authFailed = false): Provider {
  return { provide: RunnerLiveUpdates, useValue: { status: signal(status), authFailed: signal(authFailed) } };
}

/** A full `DashboardView` body (issue #311) — every field this header's own
 * dashboard read touches (`runner.capacities`, `environments`), plus every
 * other section a plausible/empty default. */
function dashboardBody(overrides: Partial<runnerApi.DashboardView> = {}): runnerApi.DashboardView {
  return {
    runner: {
      runner_id: 'runner-local',
      workspace_id: 'workspace-local',
      pause: { local: false, hub: false, effective: false },
      capacities: { max_agents: 4, used: 0, free: 4 },
      hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: null, buffer_depth: 0 },
      last_tick_at: null,
    },
    environments: { items: [] },
    asks: { items: [] },
    escalations: { items: [] },
    takeovers: { items: [] },
    facts: { items: [] },
    fleet_summary: null,
    ...overrides,
  };
}

async function render(extraProviders: Provider[] = []) {
  await TestBed.configureTestingModule({
    imports: [AppHeader],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      provideRouter([]),
      ...extraProviders,
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(AppHeader);
  await settle(fixture);
  return fixture;
}

describe('AppHeader', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
  });

  afterEach(() => stub.restore());

  it('shows ok in the header once the runner local API responds (issue #131)', async () => {
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('ok');
  });

  it('shows offline in the header when the runner local API is unreachable (issue #131)', async () => {
    stub = stubRequestClient(runnerClient, (method, path) =>
      method === 'GET' && path === '/api/dashboard' ? stubError(503, { detail: 'down' }) : { items: [] },
    );
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('offline');
  });

  it('shows reconnecting… while the live stream retries a drop, even though the dashboard read is ok (blizzard#333)', async () => {
    const fixture = await render([fakeLiveUpdates('reconnecting')]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('reconnecting…');
  });

  it('shows degraded once the stream has exhausted its bounded re-arm, even though the dashboard read is ok (blizzard#333)', async () => {
    const fixture = await render([fakeLiveUpdates('closed', true)]);
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="conn"]')?.textContent).toContain('degraded');
  });

  it('folds the runner status and environments reads into the header stat cells (issue #131)', async () => {
    stub = stubRequestClient(runnerClient, (method, path) =>
      method === 'GET' && path === '/api/dashboard'
        ? dashboardBody({
            runner: {
              runner_id: 'runner-local',
              workspace_id: 'workspace-local',
              pause: { local: false, hub: false, effective: false },
              capacities: { max_agents: 2, used: 1, free: 1 },
              hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: null, buffer_depth: 0 },
              last_tick_at: null,
            },
            environments: {
              items: [
                { environment_id: 'alpha', chunk_id: 'ch_1', held_since: '2026-07-16T11:18:00.000Z' },
                { environment_id: 'beta', chunk_id: null, held_since: null },
                { environment_id: 'gamma', chunk_id: null, held_since: null },
                { environment_id: 'delta', chunk_id: null, held_since: null },
              ],
            },
          })
        : { items: [] },
    );
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="stat-envs"]')?.textContent?.trim()).toBe('1/4');
    expect(el.querySelector('[data-testid="stat-agents"]')?.textContent?.trim()).toBe('1/2');
  });

  it('withholds the header stat cells before the first read resolves, rather than a misleading 0/0', async () => {
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
    await TestBed.configureTestingModule({
      imports: [AppHeader],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([]),
      ],
    }).compileComponents();
    const fixture = TestBed.createComponent(AppHeader);
    // Right after creation neither the status nor the environments read has resolved
    // yet — the same gap the hub header's own spendToday cell withholds itself for.
    fixture.detectChanges();
    let el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="stat-envs"]')).toBeNull();
    expect(el.querySelector('[data-testid="stat-agents"]')).toBeNull();

    await settle(fixture);
    el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="stat-envs"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="stat-agents"]')).not.toBeNull();
  });

  it('renders the shared 48px board header, not a bespoke local one (issue #131)', async () => {
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board-header"]')).not.toBeNull();
  });

  it('buries the appearance switcher behind the header menu, closed by default', async () => {
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
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

  it('renders the shared avatar-circle trigger on the header menu (issue #132)', async () => {
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
    const fixture = await render();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="local-panel-menu"] fleet-kit-avatar')).not.toBeNull();
  });

  /*
   * The desktop profile menu owns this shell's logout since #163's narrow tier
   * hides the identity block outright — a header cell that disappears must not
   * take the only way out with it.
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
      (fixture.nativeElement as HTMLElement).querySelector<HTMLElement>('[data-testid="local-panel-menu"]')?.click();
      await settle(fixture);
      return document.body.querySelector('[data-testid="local-panel-menu-panel"]')!;
    };

    it('carries Log out as a menu item, so it survives the header cell collapsing', async () => {
      withSession({ auth_enabled: true, username: 'alice' });
      const fixture = await render();

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

  /*
   * This shell's half of the header's tiered collapse (issue #163). `BoardHeader`
   * pins its trailing cluster `flex: none`, but it can only collapse the cells it
   * renders itself — and this header projects a pause control and an identity
   * block in beside the menu, where the hub board projects only the menu. jsdom
   * parses `@container` rules without evaluating them, so these resolve the
   * rules this component actually ships at a given header width rather than
   * trusting `getComputedStyle`, which reports the wide-tier value at every one.
   */
  describe('trailing-cluster collapse at narrow header widths (issue #163)', () => {
    const at = (el: HTMLElement, selector: string, width: number) =>
      hiddenAtContainerWidth(el.querySelector(selector)!, { containerName: 'board-header', width });

    beforeEach(() => {
      stub = stubRequestClient(runnerClient, () => ({ items: [] }));
    });

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
      const el = (await render()).nativeElement as HTMLElement;

      expect(getComputedStyle(el.querySelector<HTMLElement>('local-identity')!).minWidth).toBe('0px');
      expect(getComputedStyle(el.querySelector<HTMLElement>('.menu')!).flexShrink).toBe('0');
      expect(getComputedStyle(el.querySelector<HTMLElement>('local-pause-control')!).flexShrink).toBe('0');
    });

    it('rides the same named container the header declares, not a viewport media query', async () => {
      const el = (await render()).nativeElement as HTMLElement;

      expect(getComputedStyle(el.querySelector('.mc-header')!).containerName).toBe('board-header');
      expect(at(el, 'local-pause-control', 700)).toBe(false);
      expect(at(el, 'local-pause-control', 699)).toBe(true);
    });
  });
});
