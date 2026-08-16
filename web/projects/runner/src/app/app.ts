import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { RunnerLiveUpdates, SessionRecovery, SessionRecoveryView } from 'local-panel';

import { ViewportService } from 'fleet';
import { AppNav } from './nav/app-nav';
import { MobileTabBar } from './nav/mobile-tab-bar';

/**
 * The runner local-panel app — a thin entrypoint that renders the routed
 * panel shell. It composes the shared fleet library (design tokens, and the
 * fleet views as they arrive) plus the runner-only local-panel library.
 *
 * Routed tab shell (issue #313): a top {@link AppNav} strip (desktop) or a
 * persistent bottom {@link MobileTabBar} (mobile) frames the routed content
 * below `<router-outlet>` — `/board` (today's panel) and `/events` (the fact
 * log at full width, `app.routes.ts`). The mobile/desktop fork is picked once
 * here, mirroring the hub app-root's own "adaptive shells over shared guts"
 * placement (`../docs/designs/mobile/README.md`) — `LocalPanelLayout`'s own
 * internal desktop/mobile fork (which shell renders the panel's *contents*)
 * is a separate, narrower concern this fork does not replace.
 *
 * Session-aware fork (issue #312), mirroring the hub shell's own auth fork in
 * spirit though not in shape — `local-panel.ts` is already at the `max-lines`
 * cap, so the fork sits here instead: {@link SessionRecovery.recovering} renders
 * {@link SessionRecoveryView} in place of the routed shell for the one condition
 * where there is no session left to render it against (a bounce already
 * attempted, and a further no-session `401` arrived before it completed). Every
 * other case — including an upstream `401` the seam cannot fix — renders the
 * shell exactly as before, since only the seam's own classification ever sets
 * `recovering`.
 *
 * {@link RunnerLiveUpdates} starts here too (blizzard#317 Phase 4), unconditionally
 * and unlike the hub's own `authState`-gated start: the panel carries no session
 * gate of its own — every read already polls regardless of auth state, degrading to
 * its own region's `401` on an expired session — so the stream opens the same way.
 * A stream `401` is not a special case to fork the template on: `SessionRecovery`
 * already renders {@link SessionRecoveryView} once its own classification sets
 * `recovering`, and the live-updates service drives that same classification (D9)
 * on the stream's `authFailed`.
 */
@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AppNav, MobileTabBar, RouterOutlet, SessionRecoveryView],
  template: `
    @if (recovering()) {
      <local-session-recovery (retry)="onRetry()" />
    } @else {
      <div class="shell">
        @if (mobile()) {
          <router-outlet />
          <app-mobile-tab-bar />
        } @else {
          <app-nav />
          <router-outlet />
        }
      </div>
    }
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
    }
    .shell {
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
  `,
})
export class App {
  private readonly sessionRecovery = inject(SessionRecovery);
  private readonly liveUpdates = inject(RunnerLiveUpdates);
  private readonly viewport = inject(ViewportService);

  protected readonly recovering = this.sessionRecovery.recovering;

  /** The app-root-level shell fork — picked once here, mirroring the hub
   * app-root's own `mobile` computed (`../hub/src/app/app.ts`). */
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  constructor() {
    this.liveUpdates.start();
  }

  protected onRetry(): void {
    this.sessionRecovery.retry();
  }
}
