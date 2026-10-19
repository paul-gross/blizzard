import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { RunnerLiveUpdates } from '../core/live/runner-live-updates';
import { SessionRecovery } from '../core/identity/session-recovery';
import { SessionRecoveryView } from '../core/identity/session-recovery-view';

import { AppShell, FlagStore, ViewportService } from 'fleet';
import { AppHeader } from './nav/app-header';
import { AppNav } from './nav/app-nav';
import { MobileTabBar } from './nav/mobile-tab-bar';
import { MobileTitlebar } from './nav/mobile-titlebar';
import { PanelFiltersBar } from './nav/panel-filters-bar';

/**
 * The runner app — a thin entrypoint that renders the routed
 * panel shell. It composes the shared fleet library (design tokens, and the
 * fleet views as they arrive) plus the runner's own board feature folders.
 *
 * The app header — {@link AppHeader} (desktop) or {@link MobileTitlebar}
 * (mobile) — is app-root chrome, persisting across every page exactly the way the hub's own
 * `BoardHeader`/`MobileTitlebar` pair does. The shared {@link AppShell}
 * (`fleet`) enforces the order by construction: header above nav above routed
 * content, the same fixed slot order the hub app root composes it in, so the
 * two apps cannot drift into different orderings.
 *
 * Routed tab shell: a top {@link AppNav} strip (desktop) or a
 * persistent bottom {@link MobileTabBar} (mobile) frames the routed content
 * below `<router-outlet>` — the board, events, and host pages
 * (`app.routes.ts`); on mobile the panel's filters ({@link PanelFiltersBar}) sit under
 * the titlebar, where the desktop strip carries them. The mobile/desktop fork is picked once
 * here, mirroring the hub app-root's own "adaptive shells over shared guts"
 * placement (`../docs/designs/mobile/README.md`).
 *
 * Session-aware fork: {@link SessionRecovery.recovering} renders
 * {@link SessionRecoveryView} in place of the routed shell for the one condition
 * where there is no session left to render it against (a bounce already
 * attempted, and a further no-session `401` arrived before it completed).
 *
 * {@link RunnerLiveUpdates} starts here unconditionally.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AppHeader, AppNav, AppShell, MobileTabBar, MobileTitlebar, PanelFiltersBar, RouterOutlet, SessionRecoveryView],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  private readonly sessionRecovery = inject(SessionRecovery);
  private readonly liveUpdates = inject(RunnerLiveUpdates);
  private readonly viewport = inject(ViewportService);

  /** Injected here so the host's one flags read fires at start, whichever shell renders. */
  private readonly flags = inject(FlagStore);

  protected readonly recovering = this.sessionRecovery.recovering;

  /** The app-root-level shell fork — picked once here, mirroring the hub
   * app-root's own `mobile` computed (`../hub/src/app/shell/app.ts`). */
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  constructor() {
    this.liveUpdates.start();
  }

  protected onRetry(): void {
    this.sessionRecovery.retry();
  }
}
