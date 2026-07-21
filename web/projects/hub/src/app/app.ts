import { ChangeDetectionStrategy, Component, afterRenderEffect, computed, effect, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';
import {
  BoardHeader,
  FleetLiveUpdates,
  GuestLobby,
  ViewportService,
  hasPermission,
  injectAuthProvidersQuery,
  injectHubChunksQuery,
  injectHubFleetSpendQuery,
  injectHubHealthQuery,
  injectLogoutMutation,
  injectMeQuery,
  redirectToLogin,
} from 'fleet';

import { startOfLocalDayIso } from './local-day';
import { AppNav } from './nav/app-nav';
import { MobileTabBar } from './nav/mobile-tab-bar';
import { MobileTitlebar } from './nav/mobile-titlebar';

/**
 * The hub app shell — the titlebar, the top nav (or their mobile shell
 * counterparts), and the routed content.
 *
 * The window is a full-width titlebar ({@link BoardHeader} — the brand, the live
 * fleet counts, and the hub connection) over a tab strip ({@link AppNav}), with the
 * active route rendered below via `<router-outlet>` — desktop mode. In mobile mode
 * (`ViewportService.mode`) both are replaced by {@link MobileTitlebar}, and a
 * persistent {@link MobileTabBar} renders below the routed content (mock screen C,
 * `../docs/designs/mobile/core-flows.html`) — the fork happens once, here, at the
 * app root, the same "pick it once" rule the route table already follows for the
 * `board` route itself (`app.routes.ts`).
 *
 * - the {@link FleetLiveUpdates} spine subscribes to `GET /api/events/stream` and
 *   invalidates the reads on every hub fact, so the whole app streams live;
 * - both the SSE subscription and the TanStack `QueryClient` (the app-config
 *   singleton, see `app.config.ts`) are scoped to this root component, never to a
 *   routed page, so navigating between tabs never restarts the stream or drops the
 *   query cache.
 *
 * Session-aware UI (issue #93): {@link authState} gates what renders at the very
 * top, above the mobile/desktop fork — `/api/me` (`injectMeQuery`) is the one read
 * that decides it:
 *
 * - **`unauthenticated`** (no/expired session) redirects to `/login` (an effect
 *   below, plus the 401 interceptor on the same underlying response) and renders a
 *   bare `<router-outlet>` — but only once the router has actually landed there
 *   (`onLoginRoute`); router navigation is async, so a session dropping mid-route
 *   withholds the outlet for that one tick rather than let it re-activate the
 *   previous, now-ungated route. `/login` carries its own full-page chrome, so the
 *   header/nav/tab-bar are withheld here regardless;
 * - **`lobby`** (a resolved identity with an **empty** permission set — `guest`,
 *   before an admin grants anything, #94) renders {@link GuestLobby} instead of the
 *   board — "signed in, awaiting access", never a board silently 403ing every read;
 * - **`ready`** (at least one permission) renders the shell exactly as before. Under
 *   `auth.mode = "none"` `/api/me` always resolves the implicit operator (every
 *   permission), so this is the only branch that mode ever reaches — unchanged
 *   behavior, no login page, admin nav hidden (below).
 *
 * The SSE spine only starts once `ready` (a `guest`/unauthenticated stream would
 * just 401/403 immediately), and its {@link FleetLiveUpdates.authFailed} channel — a
 * session that expired mid-stream — routes back to `/login` the same way the
 * interceptor does, within the one reconnect cycle that surfaces it.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [BoardHeader, AppNav, MobileTitlebar, MobileTabBar, RouterOutlet, GuestLobby],
  template: `
    @switch (authState()) {
      @case ('unauthenticated') {
        @if (onLoginRoute()) {
          <router-outlet />
        } @else {
          <!-- The redirect to /login (the 401 interceptor, or this class's own
               effect below) has not landed yet — withhold the outlet rather than
               let it re-activate whatever route was current before the session
               dropped (e.g. the board), which would flash ungated content. -->
          <div class="boot" data-testid="auth-loading"></div>
        }
      }
      @case ('lobby') {
        <fleet-guest-lobby [me]="me()" (logout)="onLogout()" />
      }
      @case ('ready') {
        <div class="layout">
          @if (mobile()) {
            <app-mobile-titlebar [live]="streamLive()" />
          } @else {
            <fleet-board-header
              [connection]="connection()"
              [chunks]="chunks()"
              [spendToday]="spendToday.data() ?? null"
            />
            <app-nav [showAdmin]="canManageUsers()" (logout)="onLogout()" />
          }
          <router-outlet />
          @if (mobile()) {
            <app-mobile-tab-bar />
          }
        </div>
      }
      @default {
        <!-- 'loading' — the first /api/me round trip has not resolved yet. No
             header/nav until the session state is known, so nothing gated flashes
             visible-then-hidden. -->
        <div class="boot" data-testid="auth-loading"></div>
      }
    }
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
    /* The titlebar and nav span the window, and the routed page fills everything
       under them. The layout is height-capped to the viewport and the routed page
       scrolls its own content, so the page itself never scrolls — an operator's
       board does not move under them. */
    .layout {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    /* router-outlet is an empty anchor element the router inserts routed
       components after — it carries no visual size of its own. */
    router-outlet {
      display: none;
    }
    .boot {
      height: 100%;
    }
  `,
})
export class App {
  private readonly health = injectHubHealthQuery();
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly live = inject(FleetLiveUpdates);
  private readonly viewport = inject(ViewportService);
  private readonly router = inject(Router);
  private readonly meQuery = injectMeQuery();
  private readonly logoutMutation = injectLogoutMutation();
  private readonly providersQuery = injectAuthProvidersQuery();

  /** The fleet's spend-today read (issue #60) — `since` is local start-of-day,
   * recomputed each time the query re-derives its key (a day rollover moves the
   * window forward, same as any other calendar-relative read). */
  protected readonly spendToday = injectHubFleetSpendQuery(() => startOfLocalDayIso());

  /** The app-root-level shell fork (`../docs/designs/mobile/README.md`'s
   * "adaptive shells over shared guts") — picked once here, mirroring the
   * `board` route's own guard-based fork in `app.routes.ts`. */
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  /** The resolved identity, or `null` while pending/unauthenticated. */
  protected readonly me = computed(() => this.meQuery.data() ?? null);

  /** The top-level session gate (issue #93) — see the class docstring. */
  protected readonly authState = computed<'loading' | 'unauthenticated' | 'lobby' | 'ready'>(() => {
    if (this.meQuery.isPending()) return 'loading';
    const me = this.me();
    if (me === null) return 'unauthenticated';
    return me.permissions.length === 0 ? 'lobby' : 'ready';
  });

  /** The router's current URL as a signal — read only to decide whether the
   * `unauthenticated` branch's `<router-outlet>` is safe to render yet (see the
   * template). Router navigation is async, so a session dropping mid-route does not
   * make `/login` current the same tick `authState` flips. */
  private readonly routerUrl = toSignal(
    this.router.events.pipe(
      filter((event) => event instanceof NavigationEnd),
      map(() => this.router.url),
    ),
    { initialValue: this.router.url },
  );

  protected readonly onLoginRoute = computed(() => this.routerUrl().startsWith('/login'));

  /** Whether a login mechanism is even configured — `GET /api/auth/providers`
   * answers `[]` outright under `auth.mode = "none"` (the hub's own answer, never
   * re-derived here; `/api/me`'s shape carries no `mode` field to read instead). */
  private readonly authEnabled = computed(() => (this.providersQuery.data()?.length ?? 0) > 0);

  /** Gates the `Admin` nav entry — `user:manage`, held by `admin`/`superuser` (never
   * `contributor`). Also requires {@link authEnabled}: under `auth.mode = "none"`
   * the implicit operator carries every permission including this one, but there
   * are no real users for an admin page to administer — the nav stays hidden
   * exactly as it always has. */
  protected readonly canManageUsers = computed(() => hasPermission(this.me(), 'user:manage') && this.authEnabled());

  constructor() {
    // Open the SSE stream and wire it to the query cache once authenticated with at
    // least one permission — `start()` is idempotent, so a flip back to `ready`
    // (e.g. after a role change, #94) resumes it. `afterRenderEffect` (not a plain
    // `effect`): `FleetLiveUpdates.start()` calls `effect()` itself, and Angular
    // forbids calling `effect()` from within another effect's synchronous callback
    // (`NG0602`) — the render-phase effect runs outside that reactive context.
    afterRenderEffect(() => {
      if (this.authState() === 'ready') this.live.start();
    });

    // Drive the redirect to /login directly off the resolved session state — the
    // 401 interceptor also fires on the same underlying `/api/me` 401, but this is
    // the primary, always-correct trigger regardless of which read surfaced it.
    effect(() => {
      if (this.authState() === 'unauthenticated' && !this.onLoginRoute()) redirectToLogin(this.router);
    });

    // The SSE spine's explicit auth-failure channel (issue #93) — a session that
    // expired mid-stream routes back to `/login`, the same seam the 401 interceptor
    // uses, within the one reconnect cycle that surfaced it.
    effect(() => {
      if (this.live.authFailed()) redirectToLogin(this.router);
    });
  }

  protected onLogout(): void {
    this.logoutMutation.mutate();
  }

  /** Header status: the live stream's connection state, falling back to the health read. */
  protected readonly connection = computed(() => {
    const streamState = this.live.status();
    if (streamState === 'reconnecting') return 'reconnecting…';
    if (this.health.isPending()) return 'connecting…';
    if (this.health.isError()) return 'offline';
    return this.health.data()?.['status'] ?? 'ok';
  });

  /** The live fleet chunk list; empty until the first read resolves. */
  protected readonly chunks = computed(() => this.chunksQuery.data() ?? []);

  /** Whether the SSE spine is currently connected — {@link MobileTitlebar}'s
   * live dot, the same `open` check the glance board's own vitals strip uses
   * (`board/glance/glance-board.ts`). */
  protected readonly streamLive = computed(() => this.live.status() === 'open');
}
