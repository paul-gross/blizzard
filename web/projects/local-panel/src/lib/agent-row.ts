import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import type { runnerApi } from 'fleet';

/** Bounded tolerance for benign browser-vs-hub clock skew (`bzh:utc-instants`). */
const SKEW_TOLERANCE_MS = 60_000;

/**
 * Formats a millisecond age as the mockup's `.hb-age` shorthand (`-34s` / `-12m` /
 * `-1h04m`, `runner-panel.html`). Only ever called with a `deltaMs` already inside
 * the bounded skew tolerance (see {@link AgentRow.heartbeatAge}), so a small negative
 * input — genuine browser-vs-hub clock skew, never more than the tolerance — floors
 * at zero rather than rendering a confusing double-negative age.
 */
function formatHeartbeatAge(deltaMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(deltaMs / 1000));
  if (totalSeconds < 60) return `-${totalSeconds}s`;
  const totalMinutes = Math.floor(totalSeconds / 60);
  if (totalMinutes < 60) return `-${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `-${hours}h${String(minutes).padStart(2, '0')}m`;
}

/**
 * One active lease — presentational, `OnPush`. Shaped like the mockup's `.lease`
 * row (`runner-panel.html`): lease id + chunk id + epoch on the first line, the
 * derived `state` right-aligned with its heartbeat age; node/env/pid/session on
 * the second. `chunk_id` alone is the row's identity in this phase — issue titles
 * layer on in #29's follow-up phase, not here.
 *
 * `data-lease-id` is a stable hook for #29 to select a row by; deliberately no
 * `(select)` output or `role="button"` yet — an affordance with no target is dead
 * code until #29 gives it one.
 */
@Component({
  selector: 'fleet-agent-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="lease c-row" data-testid="agent-row" [attr.data-lease-id]="agent().lease_id">
      <div class="l1">
        <span class="lid">
          {{ agent().lease_id }} <small>· {{ agent().chunk_id }} · epoch {{ agent().epoch }}</small>
        </span>
        <span class="st-wrap">
          <span class="st" [class]="stateClass()" [attr.data-testid]="'agent-state'">{{ stateLabel() }}</span>
          <span class="hb-age" [class.stale]="isStale()" [class.dim]="isParked()" data-testid="agent-hb-age">
            {{ heartbeatAge() }}
          </span>
        </span>
      </div>
      <div class="l2">
        node <b>{{ agent().node_name }}</b> · env <b>{{ agent().environment_id ?? '—' }}</b> · pid
        <b>{{ agent().pid ?? '—' }}</b> · session <b>{{ agent().session_id ?? '—' }}</b>
      </div>
    </div>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .lease {
      padding: 5px 8px;
      border-bottom: 1px solid var(--line);
    }
    .l1 {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 8px;
    }
    .lid {
      color: var(--amber);
      font-size: 12px;
    }
    .lid small {
      color: var(--label);
      font-size: 9.5px;
    }
    .st-wrap {
      display: flex;
      align-items: baseline;
      gap: 6px;
      flex: none;
    }
    .st {
      font-size: 9px;
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
    .hb-age {
      width: 46px;
      text-align: right;
      font-size: 10px;
      color: var(--label);
    }
    .hb-age.stale {
      color: var(--red);
    }
    .hb-age.dim {
      color: var(--label-dim);
    }
    .l2 {
      color: var(--label);
      font-size: 10px;
      margin-top: 2px;
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

  /** `st-running` / `st-stale` / `st-parked` / `st-spawning` / `st-exited`. */
  protected readonly stateClass = computed(() => `st-${this.agent().state}`);

  protected readonly stateLabel = computed(() => this.agent().state.toUpperCase());

  protected readonly isStale = computed(() => this.agent().state === 'stale');
  protected readonly isParked = computed(() => this.agent().state === 'parked');

  /**
   * `-34s` / `-12m` / `-1h04m` from `last_heartbeat_at` vs `Date.now()`.
   *
   * `spawning` leases have never heartbeat (`last_heartbeat_at` is `null` until the
   * first beat lands, D-092) — rendering `-0s`, or an age computed off `created_at`,
   * would claim a heartbeat fact that doesn't exist yet, so this renders `—`
   * instead. The backend's own `created_at` fallback (`derive_lease_state`) is a
   * *reaping* rule, not a heartbeat fact, and must not leak into this label.
   *
   * Liveness is decided where both instants share one clock — the hub, via the
   * server-derived `state` this row already renders (`agent-state`, D-105 admits a
   * Tailnet-reachable browser, so "same machine, no skew" isn't a given); this
   * label is decoration computed against the *browser's* clock, and a browser's
   * clock must never make a correctness call (`bzh:utc-instants`). A small negative
   * delta (up to {@link SKEW_TOLERANCE_MS}) is benign browser-vs-hub clock skew, so
   * it reads as `-0s`, same as `seenLabel` in `runner-strip.ts`. Past that bound it
   * is not skew — it is the naive-timestamp failure the rule exists to catch — so
   * this falls through to the same `—` the "never heartbeat yet" case renders,
   * leaving the adjacent `state` to carry the meaning instead of guessing.
   */
  protected readonly heartbeatAge = computed<string>(() => {
    const lastHeartbeatAt = this.agent().last_heartbeat_at;
    if (lastHeartbeatAt === null) return '—';
    const beatMs = Date.parse(lastHeartbeatAt);
    if (Number.isNaN(beatMs)) return '—';
    const deltaMs = Date.now() - beatMs;
    if (deltaMs < -SKEW_TOLERANCE_MS) return '—';
    return formatHeartbeatAge(deltaMs);
  });
}
