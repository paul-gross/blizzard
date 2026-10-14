import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { KitTab, KitTabStrip } from 'fleet';

import { injectActiveRunner } from '../../core/active-runner';
import { RunnerSwitcher } from './runner-switcher';

/**
 * The panel's top-level tab strip — `Host`, then the active runner's `Board`, `Events`,
 * and `Machine`, one `routerLink` each, wearing the same {@link KitTabStrip}/{@link KitTab}
 * chrome the hub's own `app-nav.ts` wears — one tab treatment, not two — and the
 * {@link RunnerSwitcher} at its end. The runner's tabs render only while a runner is
 * active. `queryParamsHandling="preserve"` carries the board's `?chunk=` selection (the
 * shared `injectChunkUrlSelection`) across a Board → Events → Board round trip rather
 * than dropping it on navigation.
 */
@Component({
  selector: 'app-nav',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip, RouterLink, RouterLinkActive, RunnerSwitcher],
  templateUrl: './app-nav.html',
  styleUrl: './app-nav.css',
})
export class AppNav {
  private readonly activeRunner = injectActiveRunner();

  /** The active runner's own path segments, `null` on a host page. */
  protected readonly runnerBase = computed<readonly string[] | null>(() => {
    const id = this.activeRunner();
    return id === null ? null : ['/runners', id];
  });
}
