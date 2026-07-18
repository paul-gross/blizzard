import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { compactRef, type runnerApi } from 'fleet';

import { HeartbeatFreshness } from './heartbeat-freshness';

/**
 * One active lease — presentational, `OnPush`. Shaped like the discovery
 * mock's `.lease` row: compact refs (`L-ZPRR · C-7S5D · epoch 2` —
 * `compactRef`, the app-wide short-name mechanism) with the server-derived
 * `state` right-aligned on the first line, `node / env / pid / session` on the
 * second, and a {@link HeartbeatFreshness} bar under both. Deliberately free of
 * issue chips/titles — the lease list is the *liveness* rail; what a chunk is
 * about lives on the machine-chunks list, which carries the PM enrichment.
 *
 * `data-lease-id` remains a stable hook for the e2e tier to select a row by
 * (`bzh:sweep-release-only-tiers` — `data-*` is the sanctioned e2e seam). The
 * row is the selection affordance: `role="button"` + `tabindex="0"`,
 * click/Enter/Space all emit {@link selectLease}, and `selected` reflects the
 * container's current mark.
 */
@Component({
  selector: 'local-agent-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HeartbeatFreshness],
  template: `
    <div
      class="lease"
      data-testid="agent-row"
      [attr.data-lease-id]="agent().lease_id"
      [class.selected]="selected()"
      role="button"
      tabindex="0"
      (click)="onSelect()"
      (keydown.enter)="onSelect($event)"
      (keydown.space)="onSelect($event)"
    >
      <div class="l1">
        <span class="lid">
          {{ leaseRef() }} <small>· {{ chunkRef() }} · epoch {{ agent().epoch }}</small>
        </span>
        <span class="st" [class]="stateClass()" [attr.data-testid]="'agent-state'">{{ stateLabel() }}</span>
      </div>
      <div class="l2">
        node <b>{{ agent().node_name }}</b> · env <b>{{ agent().environment_id ?? '—' }}</b> · pid
        <b>{{ agent().pid ?? '—' }}</b> · session <b>{{ agent().session_id ?? '—' }}</b>
      </div>
      <local-heartbeat-freshness [lastHeartbeatAt]="agent().last_heartbeat_at" [stale]="isStale()" />
    </div>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .lease {
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
      cursor: pointer;
    }
    .lease:hover {
      background: var(--panel-deep);
    }
    .lease.selected {
      background: var(--bezel-hi);
    }
    .lease:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: -1px;
    }
    .l1 {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 8px;
    }
    .lid {
      color: var(--amber);
      font-size: var(--fs-base);
    }
    .lid small {
      color: var(--label);
      font-size: var(--fs-label);
    }
    .st {
      flex: none;
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    .st-running {
      color: var(--green);
    }
    .st-stale {
      color: var(--red);
    }
    .st-parked {
      color: var(--amber-hi);
    }
    .st-spawning {
      color: var(--cyan);
    }
    .st-exited {
      color: var(--label-dim);
    }
    .st-closed {
      color: var(--label-dim);
    }
    .l2 {
      color: var(--label);
      font-size: var(--fs-xs);
      margin: 2px 0 4px;
    }
    .l2 b {
      color: var(--cyan);
      font-weight: normal;
    }
  `,
})
export class AgentRow {
  /** The lease this row renders, incl. the server-derived `state` (issue #28). */
  readonly agent = input.required<runnerApi.LeaseView>();

  /** Whether a container considers this row the current selection (issue #29). */
  readonly selected = input(false);

  /**
   * Emits this row's `lease_id` on click, Enter, or Space (issue #29). Named
   * `selectLease`, matching `board-shell.ts`'s `selectChunk` — the house
   * convention for a row-select output — rather than the native `select`
   * DOM event name.
   */
  readonly selectLease = output<string>();

  /** Emits {@link selectLease}; `event` is only present for the keyboard bindings, where it is
   * prevented so Space doesn't also scroll the page. */
  protected onSelect(event?: Event): void {
    event?.preventDefault();
    this.selectLease.emit(this.agent().lease_id);
  }

  protected readonly leaseRef = computed(() => compactRef(this.agent().lease_id));
  protected readonly chunkRef = computed(() => compactRef(this.agent().chunk_id));

  /** `st-running` / `st-stale` / `st-parked` / `st-spawning` / `st-exited` / `st-closed`. */
  protected readonly stateClass = computed(() => `st-${this.agent().state}`);

  protected readonly stateLabel = computed(() => this.agent().state.toUpperCase());

  protected readonly isStale = computed(() => this.agent().state === 'stale');
}
