import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS, ageMs, formatAge, injectNowSignal } from 'fleet';

/**
 * Heartbeat freshness as a draining bar: 100% for any age at or under
 * {@link RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS}, 0% at {@link staleAfterSeconds}.
 * The bar only decorates the server-derived `stale` state; it decides nothing.
 *
 * Healthy heartbeat gaps run seconds to minutes against a reap threshold of about
 * an hour, so a linear drain would pin every healthy lease near 100%. Past the
 * anchor the drain is logarithmic — `1 - log(1+age)/log(1+threshold)` — so the bar
 * moves in the band where a lease actually lives. The anchor is the interval this
 * row's heartbeat is sampled at, the finest age the bar can back.
 *
 * With no heartbeat yet, or a timestamp ahead of the browser clock beyond the skew
 * tolerance, it renders an empty track plus `—` (`bzh:utc-instants`).
 */
@Component({
  selector: 'app-heartbeat-freshness',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './heartbeat-freshness.html',
  styleUrl: './heartbeat-freshness.css',
})
export class HeartbeatFreshness {
  /** The lease's `last_heartbeat_at` ISO instant, or null before the first beat. */
  readonly lastHeartbeatAt = input.required<string | null>();

  /** The lease's `stale_after_seconds` — how old a heartbeat may read before the lease is stale. */
  readonly staleAfterSeconds = input.required<number>();

  /** Whether the server already derived this lease `stale` — colors the bar red. */
  readonly stale = input(false);

  /** Ticks once a second so the bar drains between fresh `lastHeartbeatAt` values. */
  private readonly now = injectNowSignal(1000);

  protected readonly freshAgeMs = computed(() => ageMs(this.lastHeartbeatAt(), this.now()));

  protected readonly percent = computed<number>(() => {
    const age = this.freshAgeMs();
    if (age === null) return 0;
    // The bar cannot resolve an age finer than its own anchor's sampling
    // interval, so an age within it drains to nothing before the log curve
    // ever sees it.
    const resolvedAge = Math.max(0, age - RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS);
    // Second-granular: in ms the log ratio compresses the useful band, and
    // sub-second precision is noise here.
    const drained = Math.log1p(resolvedAge / 1000) / Math.log1p(this.staleAfterSeconds());
    return Math.round(Math.max(0, Math.min(1, 1 - drained)) * 100);
  });

  protected readonly ageLabel = computed<string>(() => {
    const age = this.freshAgeMs();
    return age === null ? '—' : formatAge(age);
  });
}
