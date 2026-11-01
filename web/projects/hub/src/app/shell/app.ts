import {
  ChangeDetectionStrategy,
  Component,
  Injector,
  afterRenderEffect,
  computed,
  effect,
  inject,
  untracked,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import {
  AppShell,
  BoardHeader,
  FleetLiveUpdates,
  FlagStore,
  KitButton,
  ViewportService,
  injectNowSignal,
  KitErrorLine,
} from 'fleet/shell';
import { ProjectLens } from '../core/project-lens';
import { NoAccess } from '../core/auth/no-access';
import { TenantContext } from '../core/auth/tenant-context';
import { TenantPicker } from '../core/auth/tenant-picker';
import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import { injectAuthProvidersQuery } from '../core/auth/providers.query';
import { injectHubChunkCountsQuery } from '../board/chunks/chunk-counts.query';
import { injectHubFleetSpendQuery } from '../board/fleet-spend/fleet-spend.query';
import { injectHubHealthQuery } from '../board/health/health.query';
import { injectLogoutMutation } from '../core/auth/logout.mutations';
import { isPublicUrl, redirectToLogin } from '../core/auth/auth-redirect';

import { readDemoConfig } from '../demo/demo-config';
import { startOfLocalDayIso, startOfPreviousLocalDayIso } from '../core/local-day';
import { injectProjectsQuery } from '../core/projects.query';
import { AppNav } from './nav/app-nav';
import { AppNavMenu } from './nav/app-nav-menu';
import { MobileTabBar } from './nav/mobile-tab-bar';
import { MobileTitlebar } from './nav/mobile-titlebar';
import { lensChoices } from './nav/project-lens.model';
import {
  type AuthState,
  authState,
  connectionLabel,
  logoutErrorText,
  selectedTenantName,
  tenancyOf,
} from './app.model';

/**
 * The hub app shell — the titlebar, the top nav (or their mobile shell
 * counterparts), and the routed content.
 *
 * The window is a full-width titlebar ({@link BoardHeader} — the brand, the live
 * fleet counts, the hub connection, and the profile menu ({@link AppNavMenu})
 * projected into its `[header-trailing]` slot) over a tab strip
 * ({@link AppNav}), with the active route rendered below via
 * `<router-outlet>` — desktop mode. In mobile mode
 * (`ViewportService.mode`) both are replaced by {@link MobileTitlebar}, and a
 * persistent {@link MobileTabBar} renders below the routed content (mock screen C,
 * `../docs/designs/mobile/core-flows.html`) — the fork happens once, here, at the
 * app root. Slot ordering is owned by the shared
 * {@link AppShell} (`fleet`).
 *
 * The {@link FleetLiveUpdates} spine and the TanStack `QueryClient` are scoped to
 * this root component, never to a routed page.
 *
 * The public pages — sign-in with its refusals, and an invitation's landing
 * ({@link isPublicUrl}) — render as a bare `<router-outlet>` whatever the session,
 * once the router has actually landed on one (`onPublicRoute`): they carry their
 * own full-page chrome, and a person already signed in opens an invitation too.
 *
 * Everywhere else, {@link authState} gates what renders at the very
 * top, above the mobile/desktop fork — `/api/me` (`injectMeQuery`) is the one read
 * that decides it, and the tenant is settled here, once, from its memberships
 * ({@link TenantContext}), before any view renders:
 *
 * - **`unauthenticated`** (no/expired session) redirects to `/login` (an effect
 *   below) and renders
 *   nothing until the router lands there; router navigation is async, so a session
 *   dropping mid-route withholds the outlet for that one tick rather than let it
 *   re-activate the previous, now-ungated route;
 * - **`no-access`** (a resolved identity holding no membership) renders only
 *   {@link NoAccess} — no header, no nav, no view;
 * - **`choose`** (several memberships, none of them this tab's tenant or the
 *   browser's last choice) renders only {@link TenantPicker}; the chosen tenant is
 *   entered and the shell follows;
 * - **`ready`** (a tenant selected — the only membership, or the remembered one)
 *   renders the shell. Inside it a tenant is always selected: every call names it,
 *   and `injectMeQuery` hands every permission check that membership's role and
 *   permissions. The header carries the tenant's name.
 *
 * The SSE spine, and the header's own tenant-scoped reads, only start once the
 * board has entered the selected tenant (a call before it would name no tenant,
 * or the wrong one), and the SSE spine's {@link FleetLiveUpdates.authFailed}
 * channel — a session that expired mid-stream — routes back to `/login` the same way
 * the interceptor does, within the one reconnect cycle that surfaces it.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    AppShell,
    BoardHeader,
    AppNav,
    AppNavMenu,
    KitButton,
    MobileTitlebar,
    MobileTabBar,
    RouterOutlet,
    NoAccess,
    TenantPicker,
    KitErrorLine,
  ],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly health = injectHubHealthQuery();
  private readonly live = inject(FleetLiveUpdates);
  private readonly viewport = inject(ViewportService);
  private readonly router = inject(Router);
  private readonly meQuery = injectMeQuery();
  private readonly logoutMutation = injectLogoutMutation();
  private readonly providersQuery = injectAuthProvidersQuery();
  private readonly injector = inject(Injector);
  private readonly tenant = inject(TenantContext);
  private readonly lens = inject(ProjectLens);

  /** The hub's flags — injected here so the one read fires at start; the header shows what the operator overrode. */
  protected readonly flags = inject(FlagStore);

  /**
   * Demo mode's config, latched **here** and now — at app construction, off the
   * browser's own URL, before the router's first navigation and before the
   * `/login` redirect below can rewrite the query string that carried it. The
   * director itself is lazy (see the effect in the constructor) and arrives too
   * late to read it for itself. Disabled for every ordinary session, which is
   * every session that did not ask for `?demo`.
   */
  private readonly demoConfig = readDemoConfig(globalThis.location?.search ?? '');

  /** The app-root-level shell fork (`../docs/designs/mobile/README.md`'s
   * "adaptive shells over shared guts") — picked once here, mirroring the
   * `board` route's own guard-based fork in `app.routes.ts`. */
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  /** The resolved identity, or `null` while pending/unauthenticated. */
  protected readonly me = computed(() => this.meQuery.data() ?? null);

  /** The tenant settled from the identity's memberships, `null` without an identity. */
  protected readonly tenancy = computed(() => tenancyOf(this.me(), this.tenant.resolver()));

  /** The top-level session gate — see the class docstring. */
  protected readonly authState = computed<AuthState>(() => authState(this.meQuery.isPending(), this.tenancy()));

  /** Whether the board has entered the tenant it settled on — every call names it from here. The header's
   * tenant-scoped reads below wait on it: before it, a read would name no tenant or a stale one. */
  protected readonly ready = computed(() => {
    const tenancy = this.tenancy();
    return tenancy?.kind === 'selected' && this.tenant.current() === tenancy.tenant.tenant_id;
  });

  /** The fleet's chunk counts for the header, in the selected tenant. */
  private readonly countsQuery = injectHubChunkCountsQuery(() => this.ready());

  /** The shared one-second clock the spend windows are cut from. */
  private readonly now = injectNowSignal();

  /** The day boundaries, derived so string equality stops the tick: the query keys
   * change only when the local day does, so a tick within the day refetches nothing. */
  private readonly dayStart = computed(() => startOfLocalDayIso(this.now()));
  private readonly previousDayStart = computed(() => startOfPreviousLocalDayIso(this.now()));

  /** The fleet's spend-today read — `since` is local start-of-day off {@link dayStart},
   * so the window rolls forward at local midnight without a reload. */
  protected readonly spendToday = injectHubFleetSpendQuery(
    () => this.dayStart(),
    undefined,
    () => this.ready(),
  );

  /** The fleet's spend-yesterday read — `[startOfPreviousLocalDayIso(now),
   * startOfLocalDayIso(now))`, both derived from the one local-day boundary helper so
   * the window rolls over with today by construction and never includes today's
   * own spend. A second, independent `injectHubFleetSpendQuery` entry — distinct
   * from {@link spendToday}'s in both `since` and `until`, so it is its own cache
   * entry rather than colliding on one. */
  protected readonly spendYesterday = injectHubFleetSpendQuery(
    () => this.previousDayStart(),
    () => this.dayStart(),
    () => this.ready(),
  );

  /** The identity's memberships — what the tenant picker offers. */
  protected readonly memberships = computed(() => this.me()?.tenants ?? []);

  /** The selected tenant's name, for the header; empty outside `ready`. */
  protected readonly tenantName = computed(() => selectedTenantName(this.tenancy()));

  /** The router's current URL as a signal — read only to decide whether a public
   * page's bare `<router-outlet>` is safe to render yet (see the template). Router
   * navigation is async, so a session dropping mid-route does not make `/login`
   * current the same tick `authState` flips. */
  private readonly routerUrl = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map(() => this.router.url),
    ),
    { initialValue: this.router.url },
  );

  /** Whether the router has landed on a public page — see the class docstring. */
  protected readonly onPublicRoute = computed(() => isPublicUrl(this.routerUrl()));

  /** Whether a login mechanism is even configured — `GET /api/auth/providers`
   * answers `[]` outright under `auth.mode = "none"` (the hub's own answer, never
   * re-derived here; `/api/me`'s shape carries no `mode` field to read instead). */
  private readonly authEnabled = computed(() => (this.providersQuery.data()?.length ?? 0) > 0);

  /** Gates the `Admin` nav entry — `user:manage` in the selected tenant, held by an
   * `admin` member (never `contributor`). Also requires {@link authEnabled}: under `auth.mode = "none"`
   * the implicit operator carries every permission including this one, but there
   * are no real users for an admin page to administer — the nav stays hidden
   * exactly as it always has. */
  protected readonly canManageUsers = computed(() => hasPermission(this.me(), 'user:manage') && this.authEnabled());

  constructor() {
    // Enter the tenant the memberships settle on — the only one, or the one this tab
    // or browser remembered — so every call names it and the choice is remembered.
    effect(() => {
      const tenancy = this.tenancy();
      if (tenancy?.kind === 'selected') untracked(() => this.tenant.enter(tenancy.tenant.tenant_id));
      // Settled on none — no membership, or a choice to make — so whatever this tab or browser remembered is no
      // membership any more: forget it rather than keep naming it.
      else if (tenancy !== null) untracked(() => this.tenant.forgetSelection());
    });

    // A project lens is resolved only in the tenant the board has entered: the lens guard waits for this.
    effect(() => {
      const ready = this.ready();
      untracked(() => this.lens.settle(ready));
    });

    // Open the SSE stream and wire it to the query cache once authenticated with at
    // least one permission — `start()` is idempotent, so a flip back to `ready`
    // (e.g. after a role change, #94) resumes it. `afterRenderEffect` (not a plain
    // `effect`) so this runs post-render rather than mid-change-detection; either way
    // `FleetLiveUpdates.start()` is itself safe to call from a reactive context — it
    // wraps its own internal `effect()` call in `untracked` (fleet-live.ts).
    afterRenderEffect(() => {
      if (this.ready()) this.live.start();
    });

    // Demo mode (`?demo=true`) — the unattended kiosk tour, gated on the same
    // `ready` state as the SSE spine and for the same reason: it drives the board
    // through real reads, which a guest or an expired session cannot make.
    //
    // Loaded dynamically so an ordinary session never pays for it: the director
    // pulls in the chunk-detail read and the artifact machinery, ~50 kB that
    // otherwise lands in the initial bundle for a feature nothing links to. The
    // import is only reached when `?demo` actually asked, and `start()` is
    // idempotent, so this effect re-running on an auth settle is a no-op.
    afterRenderEffect(() => {
      if (!this.ready() || !this.demoConfig.enabled) return;
      void import('../demo/demo-director').then(({ DemoDirector }) =>
        this.injector.get(DemoDirector).start(this.demoConfig),
      );
    });

    // Drive the redirect to /login directly off the resolved session state — the
    // 401 interceptor also fires on the same underlying `/api/me` 401, but this is
    // the primary, always-correct trigger regardless of which read surfaced it.
    effect(() => {
      if (this.authState() === 'unauthenticated' && !this.onPublicRoute()) redirectToLogin(this.router);
    });

    // The SSE spine's explicit auth-failure channel — a session that
    // expired mid-stream routes back to `/login`, the same seam the 401 interceptor
    // uses, within the one reconnect cycle that surfaced it.
    effect(() => {
      if (this.live.authFailed()) redirectToLogin(this.router);
    });
  }

  /** Whether a logout is in flight, handed to every Log out trigger so each disables. */
  protected readonly logoutPending = computed(() => this.logoutMutation.isPending());

  /** The failed logout's message, `null` while none; rendered outside any menu, which closes on trigger. */
  protected readonly logoutError = computed(() => logoutErrorText(this.logoutMutation.error()));

  /** Enters the tenant the picker chose; the shell renders in it. */
  protected onChooseTenant(tenant: string): void {
    this.tenant.enter(tenant);
  }

  protected onLogout(): void {
    this.logoutMutation.mutate();
  }

  /** The tenant's projects, read once for both lens selectors once the board has entered the tenant. */
  private readonly projectsQuery = injectProjectsQuery(() => this.ready());

  /** The lens selectors' choices — All, then the tenant's projects by name. */
  protected readonly lensChoices = computed(() => lensChoices(this.projectsQuery.data() ?? []));

  /** The slug the lens is on, `null` for All. */
  protected readonly lensCurrent = this.lens.slug;

  /** Re-navigates the view the board is on under the chosen lens. */
  protected onChooseLens(slug: string | null): void {
    void this.lens.switchTo(slug);
  }

  /** The project lens's notice — a link that named no project of the tenant, opened on All. */
  protected readonly lensNotice = this.lens.notice;

  protected dismissLensNotice(): void {
    this.lens.dismissNotice();
  }

  /** Header status: the live stream's connection state, falling back to the health read. */
  protected readonly connection = computed(() =>
    connectionLabel(this.live.status(), this.health.isPending(), this.health.isError(), this.health.data()?.['status']),
  );

  /** The live fleet chunk list; empty until the first read resolves. */
  protected readonly counts = computed(() => this.countsQuery.data() ?? null);

  /** Whether the SSE spine is currently connected — {@link MobileTitlebar}'s
   * live dot, the same `open` check the glance board's own vitals strip uses
   * (`board/glance/glance-board.ts`). */
  protected readonly streamLive = computed(() => this.live.status() === 'open');
}
