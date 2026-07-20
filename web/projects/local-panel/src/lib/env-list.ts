import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { ageMs, compactRef, formatHeldFor, KitAsyncState, KitBeacon, type KitAsyncStateValue, type runnerApi } from 'fleet';

import { injectRunnerEnvironmentsQuery } from './status.query';

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
  template: `
    <div class="wrap" data-testid="env-list">
      <fleet-kit-async-state
        [state]="triadState()"
        loadingText="LOADING…"
        errorText="ENVIRONMENTS UNAVAILABLE"
        emptyText="NO ENVIRONMENTS CONFIGURED"
        emptyTestid="env-empty"
      >
        @for (env of envs(); track env.environment_id) {
          <div class="row" data-testid="env-row" [attr.data-env-id]="env.environment_id" [attr.data-held]="isHeld(env)">
            <fleet-kit-beacon data-testid="env-beacon" [active]="isHeld(env)" tone="amber" />
            <span class="env">{{ env.environment_id }}</span>
            <span class="chunk">{{ chunkRef(env) }}</span>
            <span class="held" data-testid="env-held-for">{{ heldFor(env) }}</span>
          </div>
        }
      </fleet-kit-async-state>
    </div>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .wrap {
      position: relative;
      min-height: 40px;
    }
    .row {
      display: grid;
      grid-template-columns: 12px 64px 1fr 56px;
      align-items: baseline;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      font-size: var(--fs-sm);
    }
    fleet-kit-beacon {
      align-self: center;
    }
    .env {
      color: var(--cyan);
    }
    .chunk {
      color: var(--amber);
    }
    .held {
      text-align: right;
      color: var(--label);
      font-size: var(--fs-xs);
    }
  `,
})
export class EnvList {
  protected readonly query = injectRunnerEnvironmentsQuery();

  protected readonly envs = computed(() => this.query.data() ?? []);

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
