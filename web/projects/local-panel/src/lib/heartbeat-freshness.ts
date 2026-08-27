import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { ageMs, formatAge, injectNowSignal } from 'fleet';

import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from './polling';

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
 * Heartbeat freshness as a draining bar — 100% for any age at or under
 * {@link RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS}, 0% at the reap threshold.
 * Heartbeats ride tool calls (`POST /api/heartbeat` fires from the worker's
 * PostToolUse hook), so healthy gaps run seconds to minutes while the reap
 * threshold is an hour: a *linear* drain would pin every healthy lease at ~99%
 * and give the operator nothing. The drain past that anchor is logarithmic —
 * `1 - log(1+age)/log(1+threshold)` — so the minutes-band where a lease
 * actually lives is where the bar visibly moves, and the long tail to reap
 * drains out the rest.
 *
 * `record_heartbeat` is deliberately silent (D7, no SSE event announces it), so
 * on a healthy, actively-beating lease this bar's anchor only advances on
 * {@link RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS} (`polling.ts`) or an unrelated
 * lease-changed frame — real cadence is tighter, but the bar cannot resolve an
 * age finer than that interval. Blizzard#334 (D4): rather than render a
 * partial drain it cannot back, an age at or under the backstop interval reads
 * 100%; the curve only starts draining past it, anchored at the interval
 * itself rather than at zero age, so it tracks the poll floor if that value
 * ever moves again.
 *
 * Renders nothing bar-shaped for a lease with no heartbeat fact yet
 * (`spawning` — `last_heartbeat_at` null) or one whose timestamp reads ahead of
 * the browser clock beyond the skew tolerance: an empty track plus `—`,
 * claiming no freshness fact that doesn't exist (`bzh:utc-instants`).
 */
@Component({
  selector: 'local-heartbeat-freshness',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './heartbeat-freshness.html',
  styleUrl: './heartbeat-freshness.css',
})
export class HeartbeatFreshness {
  /** The lease's `last_heartbeat_at` ISO instant, or null before the first beat. */
  readonly lastHeartbeatAt = input.required<string | null>();

  /** Whether the server already derived this lease `stale` — colors the bar red. */
  readonly stale = input(false);

  /** Ticks once a second (issue #178) so the bar drains between polls, not just when
   * `leases.query.ts`'s backstop hands this row a fresh `lastHeartbeatAt` — see
   * `RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS` (`polling.ts`) for that anchor's own bound. */
  private readonly now = injectNowSignal(1000);

  protected readonly freshAgeMs = computed(() => ageMs(this.lastHeartbeatAt(), this.now()));

  protected readonly percent = computed<number>(() => {
    const age = this.freshAgeMs();
    if (age === null) return 0;
    // D4: the bar cannot resolve an age finer than its own anchor's sampling
    // interval, so an age within it drains to nothing before the log curve
    // ever sees it.
    const resolvedAge = Math.max(0, age - RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS);
    // Second-granular: in ms the log ratio compresses the useful band, and
    // sub-second precision is noise here.
    const drained = Math.log1p(resolvedAge / 1000) / Math.log1p(STALE_AFTER_MS / 1000);
    return Math.round(Math.max(0, Math.min(1, 1 - drained)) * 100);
  });

  protected readonly ageLabel = computed<string>(() => {
    const age = this.freshAgeMs();
    return age === null ? '—' : formatAge(age);
  });
}
