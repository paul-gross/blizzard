import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, ResolveEnd, Router, RouterOutlet } from '@angular/router';
import { ACTIVE_RUNNER } from '../core/active-runner';
import { RunnerLiveUpdates } from '../core/live/runner-live-updates';
import { SessionRecovery } from '../core/identity/session-recovery';
import { SessionRecoveryView } from '../core/identity/session-recovery-view';

import { AppShell, ViewportService } from 'fleet';
import { AppHeader } from './nav/app-header';
import { AppNav } from './nav/app-nav';
import { MobileTabBar } from './nav/mobile-tab-bar';
import { MobileTitlebar } from './nav/mobile-titlebar';
import { RunnerSwitcher } from './nav/runner-switcher';
import { routeRunnerId } from './active-runner-route';

/**
 * The runner app — a thin entrypoint that renders the routed
 * panel shell. It composes the shared fleet library (design tokens, and the
 * fleet views as they arrive) plus the runner's own board feature folders.
 *
 * The app header — {@link AppHeader} (desktop) or {@link MobileTitlebar}
 * (mobile) — is app-root chrome, persisting across the host page and every
 * runner's pages exactly the way the hub's own
 * `BoardHeader`/`MobileTitlebar` pair does. The shared {@link AppShell}
 * (`fleet`) enforces the order by construction: header above nav above routed
 * content, the same fixed slot order the hub app root composes it in, so the
 * two apps cannot drift into different orderings.
 *
 * Routed tab shell: a top {@link AppNav} strip (desktop) or a
 * persistent bottom {@link MobileTabBar} (mobile) frames the routed content
 * below `<router-outlet>` — the host page and the active runner's pages
 * (`app.routes.ts`); on mobile the {@link RunnerSwitcher} sits under the titlebar,
 * where the desktop strip carries it. The mobile/desktop fork is picked once
 * here, mirroring the hub app-root's own "adaptive shells over shared guts"
 * placement (`../docs/designs/mobile/README.md`).
 *
 * Session-aware fork: {@link SessionRecovery.recovering} renders
 * {@link SessionRecoveryView} in place of the routed shell for the one condition
 * where there is no session left to render it against (a bounce already
 * attempted, and a further no-session `401` arrived before it completed).
 *
 * {@link RunnerLiveUpdates} starts here unconditionally.
 *
 * The shell is the one reader of the route's `:runnerId`: it writes the active-runner
 * signal ({@link ACTIVE_RUNNER}) as each navigation resolves, before the routed page
 * activates, and every page, query, and nav control reads the signal instead.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AppHeader, AppNav, AppShell, MobileTabBar, MobileTitlebar, RouterOutlet, RunnerSwitcher, SessionRecoveryView],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly sessionRecovery = inject(SessionRecovery);
  private readonly liveUpdates = inject(RunnerLiveUpdates);
  private readonly viewport = inject(ViewportService);

  protected readonly recovering = this.sessionRecovery.recovering;

  /** The app-root-level shell fork — picked once here, mirroring the hub
   * app-root's own `mobile` computed (`../hub/src/app/shell/app.ts`). */
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  constructor() {
    this.liveUpdates.start();
    const router = inject(Router);
    const activeRunner = inject(ACTIVE_RUNNER);
    activeRunner.set(routeRunnerId(router.routerState.snapshot.root));
    router.events.pipe(takeUntilDestroyed()).subscribe((event) => {
      if (event instanceof ResolveEnd) activeRunner.set(routeRunnerId(event.state.root));
      else if (event instanceof NavigationEnd) activeRunner.set(routeRunnerId(router.routerState.snapshot.root));
    });
  }

  protected onRetry(): void {
    this.sessionRecovery.retry();
  }
}
