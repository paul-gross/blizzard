import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { ageMs, compactRef, formatHeldFor, KitAsyncState, KitBeacon, type KitAsyncStateValue, type runnerApi } from 'fleet';

import { injectRunnerDashboardQuery } from './status.query';

/**
 * The environments rail (issue #106): one row per environment in the runner's
 * configured pool — the wire (`GET /api/environments`) carries the full pool, so the
 * panel never invents pool facts of its own. A held row carries its chunk ref
 * (compact) and how long it has been held; an unused row carries neither. The
 * indicator is the shared {@link KitBeacon} (the board's occupied-lane style):
 * held rows throb amber, unused rows sit static grey — a throbbing indicator
 * marks activity, not idleness. The empty state renders only when the pool
 * itself is empty, not merely unheld.
 */
@Component({
  selector: 'local-env-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBeacon],
  templateUrl: './env-list.html',
  styleUrl: './env-list.css',
})
export class EnvList {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly envs = computed(() => this.query.data()?.environments?.items ?? []);

  /** The async triad's resolved state — loading/error take precedence, then
   * an empty pool (no environments configured at all), else the rows render. */
  protected readonly triadState = computed<KitAsyncStateValue>(() => {
    if (this.query.isPending()) return 'loading';
    if (this.query.isError()) return 'error';
    return this.envs().length === 0 ? 'empty' : 'ready';
  });

  protected isHeld(env: runnerApi.EnvironmentView): boolean {
    return env.chunk_id != null;
  }

  protected chunkRef(env: runnerApi.EnvironmentView): string {
    return env.chunk_id == null ? '' : compactRef(env.chunk_id);
  }

  /**
   * `42m` since the binding fact — browser-clock decoration only
   * (`bzh:utc-instants` via `ageMs`): a skew-broken timestamp renders `—`, and an
   * unheld environment (no `held_since`) renders blank rather than `—`.
   */
  protected heldFor(env: runnerApi.EnvironmentView): string {
    if (env.held_since == null) return '';
    const age = ageMs(env.held_since, Date.now());
    return age === null ? '—' : formatHeldFor(age);
  }
}
