import { ChangeDetectionStrategy, Component, computed, effect, signal } from '@angular/core';
import { ageMs, asyncState, formatAge, injectNowSignal, KitAsyncState, type runnerApi } from 'fleet';

import { LocalInfoView } from './local-info-view';
import { injectRunnerDashboardQuery } from './status.query';

/**
 * The hub-link panel **container** — the discovery mock's "hub · outbound only,
 * nothing dials in": owns `GET /api/dashboard`'s `runner` section read, the
 * resolved async-state triad, the fleet-strip latch, and the ticking clock the
 * last-flush/tick labels derive from; the presentational {@link LocalInfoView}
 * owns the facts template (`bzh:frontend-container-presentational`).
 *
 * Below the link facts is the discovery mock's fleet counts strip
 * (ready/running/waiting/needs) — a fleet-level pulse. Those counts *are* a
 * hub read, so unlike the rest of this panel they arrive through the same
 * dashboard read's `fleet_summary` section — the runner's own `GET
 * /api/fleet-summary` pass-through (issue #76), folded in by Phase 1 (issue
 * #311): the hub API allows no cross-origin browser read, so the runner
 * forwards it. `fleet_summary` is `null` exactly when that forward fails (hub
 * unreachable or unwired) — a **200** carrying a null slot, not a failed
 * request, so the strip's degraded/last-known state can no longer be read off
 * the query's own `isError()`. See {@link latchedFleet}.
 */
@Component({
  selector: 'local-info',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, LocalInfoView],
  templateUrl: './local-info.html',
  styleUrl: './local-info.css',
})
export class LocalInfo {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly view = computed(() => {
    const data = this.query.data()?.runner;
    // A malformed body (e.g. `{}` from a misrouted proxy) must render the
    // degraded state, not throw on `hub.endpoint` mid-template.
    return data?.hub && data.capacities && data.pause ? data : null;
  });

  /**
   * The fleet strip's own latch (issue #311): under the composed read, a hub
   * outage is a successful `/api/dashboard` read carrying `fleet_summary: null`
   * — TanStack sees no error and retains nothing special about the prior
   * counts, so without this the strip would blank instead of degrading. This
   * `effect()` tracks the *last non-null* `fleet_summary` seen, leaving the
   * signal untouched on a `null` read — the same "keep showing last-known"
   * behavior the old two-query shape got for free from TanStack's own error-path
   * data retention.
   */
  private readonly latchedFleet = signal<runnerApi.FleetSummaryView | null>(null);
  private readonly latchFleetSummary = effect(() => {
    const summary = this.query.data()?.fleet_summary;
    if (summary != null) this.latchedFleet.set(summary);
  });

  /** The last-known fleet counts, or `null` before the first successful read —
   * {@link latchedFleet}'s own latch, not TanStack's `data()`. */
  protected readonly fleet = computed(() => this.latchedFleet());

  /** The current read's `fleet_summary` slot is `null` (hub unreachable / not
   * wired, or no read has resolved yet) — the strip degrades to its dimmed
   * last-known state. The rest of the panel is hub-free, so it is unaffected. */
  protected readonly fleetStale = computed<boolean>(() => this.query.data()?.fleet_summary == null);

  /** The async triad's resolved state — no `'empty'` case: a resolved read
   * with a malformed body renders nothing (the `view()` null-guard in the
   * projected content), the same degraded-blank behavior as before. */
  protected readonly triadState = computed(() => asyncState(this.query, false));

  /** Ticks once a second (issue #178) so `lastFlushLabel`/`lastTickLabel` advance
   * between polls instead of sitting frozen at whatever age the last read carried —
   * both elapsed-time-derived off `status.query.ts`'s backstop; see
   * `RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS` (`polling.ts`) for that anchor's own bound. */
  private readonly now = injectNowSignal(1000);

  /** `-34s` since the last successful PULL, or `never` before first contact. */
  protected readonly lastFlushLabel = computed<string>(() => {
    const contactAt = this.view()?.hub.last_contact_at ?? null;
    if (contactAt === null) return 'never';
    const age = ageMs(contactAt, this.now());
    return age === null ? '—' : formatAge(age);
  });

  protected readonly lastTickLabel = computed<string>(() => {
    const tickAt = this.view()?.last_tick_at ?? null;
    if (tickAt === null) return '—';
    const age = ageMs(tickAt, this.now());
    return age === null ? '—' : formatAge(age);
  });
}
