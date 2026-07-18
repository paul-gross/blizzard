import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { ageMs, formatAge } from 'fleet';

/**
 * How stale a heartbeat may read before REAP calls it dead — mirrors the
 * backend's `HEARTBEAT_STALENESS_THRESHOLD` (`runner/domain/leases.py`, 1h).
 * The bar's zero point: an empty bar means "reap-pending old", exactly the
 * boundary the server-derived `stale` state flips on. Kept as a frontend
 * constant because the threshold is not on the wire; the *decision* still
 * belongs to the server's `state` — this bar only ever decorates it.
 */
export const STALE_AFTER_MS = 60 * 60_000;

/**
 * Heartbeat freshness as a draining bar — 100% the instant a beat lands, 0% at
 * the reap threshold. Heartbeats ride tool calls (`POST /api/heartbeat` fires
 * from the worker's PostToolUse hook), so healthy gaps run seconds to minutes
 * while the reap threshold is an hour: a *linear* drain would pin every healthy
 * lease at ~99% and give the operator nothing. The drain is logarithmic —
 * `1 - log(1+age)/log(1+threshold)` — so the seconds-to-minutes band where a
 * lease actually lives is where the bar visibly moves (≈50% at one minute,
 * ≈20% at ten), and the long tail to reap drains out the rest.
 *
 * Renders nothing bar-shaped for a lease with no heartbeat fact yet
 * (`spawning` — `last_heartbeat_at` null) or one whose timestamp reads ahead of
 * the browser clock beyond the skew tolerance: an empty track plus `—`,
 * claiming no freshness fact that doesn't exist (`bzh:utc-instants`).
 */
@Component({
  selector: 'fleet-heartbeat-freshness',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span class="hb" data-testid="hb-freshness">
      <span class="lbl">hb</span>
      <span class="track">
        <span
          class="fill"
          [class.stale]="stale()"
          [style.width.%]="percent()"
          data-testid="hb-fill"
          [attr.data-hb-percent]="percent()"
        ></span>
      </span>
      <span class="age" [class.stale]="stale()" data-testid="hb-age">{{ ageLabel() }}</span>
    </span>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .hb {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label-dim);
    }
    .track {
      flex: 1;
      height: 4px;
      background: var(--line);
      border-radius: 2px;
      overflow: hidden;
    }
    .fill {
      display: block;
      height: 100%;
      background: var(--green);
      border-radius: 2px;
      transition: width 600ms linear;
    }
    .fill.stale {
      background: var(--red);
    }
    .age {
      width: 52px;
      text-align: right;
      font-size: var(--fs-xs);
      color: var(--label);
    }
    .age.stale {
      color: var(--red);
    }
  `,
})
export class HeartbeatFreshness {
  /** The lease's `last_heartbeat_at` ISO instant, or null before the first beat. */
  readonly lastHeartbeatAt = input.required<string | null>();

  /** Whether the server already derived this lease `stale` — colors the bar red. */
  readonly stale = input(false);

  /**
   * Recomputes when the inputs change — the 5s leases poll hands every row a
   * fresh object, so the bar ticks at the poll cadence without its own timer.
   */
  protected readonly freshAgeMs = computed(() => ageMs(this.lastHeartbeatAt(), Date.now()));

  protected readonly percent = computed<number>(() => {
    const age = this.freshAgeMs();
    if (age === null) return 0;
    // Second-granular: in ms the log ratio compresses the useful band (a 60s-old
    // beat would read ~27% instead of ~50%), and sub-second precision is noise here.
    const drained = Math.log1p(age / 1000) / Math.log1p(STALE_AFTER_MS / 1000);
    return Math.round(Math.max(0, Math.min(1, 1 - drained)) * 100);
  });

  protected readonly ageLabel = computed<string>(() => {
    const age = this.freshAgeMs();
    return age === null ? '—' : formatAge(age);
  });
}
