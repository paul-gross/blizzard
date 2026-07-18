import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { ageMs, compactRef, formatHeldFor, KitAsyncState, type KitAsyncStateValue, type runnerApi } from 'fleet';

import { injectRunnerEnvironmentsQuery } from './status.query';

/**
 * The held-environments rail — the discovery mock's "environments · bindings
 * ride the lease" panel: every env this runner currently holds, the chunk it
 * is bound to (compact ref), and how long it has been held. The API only
 * serves *held* bindings (`GET /api/environments`), so a free pool renders as
 * the empty state, not as unlit rows — the panel never invents pool facts the
 * wire doesn't carry.
 */
@Component({
  selector: 'local-env-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  template: `
    <div class="wrap" data-testid="env-list">
      <fleet-kit-async-state
        [state]="triadState()"
        loadingText="LOADING…"
        errorText="ENVIRONMENTS UNAVAILABLE"
        emptyText="NO HELD ENVIRONMENTS — POOL FREE"
        emptyTestid="env-empty"
      >
        @for (env of envs(); track env.environment_id) {
          <div class="row" data-testid="env-row" [attr.data-env-id]="env.environment_id">
            <span class="led"></span>
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
    .led {
      align-self: center;
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--amber);
      box-shadow: 0 0 5px var(--amber-dim);
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
   * an empty pool, else the held-environment rows render. */
  protected readonly triadState = computed<KitAsyncStateValue>(() => {
    if (this.query.isPending()) return 'loading';
    if (this.query.isError()) return 'error';
    return this.envs().length === 0 ? 'empty' : 'ready';
  });

  protected chunkRef(env: runnerApi.HeldEnvironmentView): string {
    return compactRef(env.chunk_id);
  }

  /**
   * `42m` since the binding fact — browser-clock decoration only
   * (`bzh:utc-instants` via `ageMs`): a skew-broken timestamp renders `—`.
   */
  protected heldFor(env: runnerApi.HeldEnvironmentView): string {
    const age = ageMs(env.held_since, Date.now());
    return age === null ? '—' : formatHeldFor(age);
  }
}
