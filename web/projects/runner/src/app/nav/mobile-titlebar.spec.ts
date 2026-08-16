import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { runnerClient } from 'fleet';
import { type RequestClientStub, settle, stubRequestClient } from 'fleet/testing';
import { LocalIdentity } from 'local-panel';
import { vi } from 'vitest';

import { MobileTitlebar } from './mobile-titlebar';

async function render() {
  await TestBed.configureTestingModule({
    imports: [MobileTitlebar],
    providers: [
      provideZonelessChangeDetection(),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
      provideRouter([]),
    ],
  }).compileComponents();
  const fixture = TestBed.createComponent(MobileTitlebar);
  await settle(fixture);
  return fixture;
}

describe('MobileTitlebar (runner)', () => {
  let stub: RequestClientStub;

  beforeEach(() => {
    stub = stubRequestClient(runnerClient, () => ({ items: [] }));
  });

  afterEach(() => stub.restore());

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
});
