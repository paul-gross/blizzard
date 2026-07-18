import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { compactRef, type runnerApi } from 'fleet';

import { ageMs, formatHeldFor } from './age';
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
  selector: 'fleet-env-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap" data-testid="env-list">
      @if (query.isPending()) {
        <p class="status">LOADING…</p>
      } @else if (query.isError()) {
        <p class="status error">ENVIRONMENTS UNAVAILABLE</p>
      } @else if (envs().length === 0) {
        <p class="status" data-testid="env-empty">NO HELD ENVIRONMENTS — POOL FREE</p>
      } @else {
        @for (env of envs(); track env.environment_id) {
          <div class="row" data-testid="env-row" [attr.data-env-id]="env.environment_id">
            <span class="led"></span>
            <span class="env">{{ env.environment_id }}</span>
            <span class="chunk">{{ chunkRef(env) }}</span>
            <span class="held" data-testid="env-held-for">{{ heldFor(env) }}</span>
          </div>
        }
      }
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
    .status {
      position: absolute;
      left: 50%;
      top: 50%;
      transform: translate(-50%, -50%);
      white-space: nowrap;
      color: var(--label-dim);
      font-size: var(--fs-xs);
      letter-spacing: 0.12em;
    }
    .status.error {
      color: var(--red);
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
