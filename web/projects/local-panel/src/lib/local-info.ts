import { ChangeDetectionStrategy, Component, TemplateRef, computed, effect, signal } from '@angular/core';
import { ageMs, asyncState, formatAge, injectNowSignal, KitAsyncState, type KitFact, KitFactList, type runnerApi } from 'fleet';

import { injectRunnerDashboardQuery } from './status.query';

/**
 * The hub-link panel — the discovery mock's "hub · outbound only, nothing
 * dials in": the configured hub endpoint, derived reachability, last flush
 * (last successful PULL contact), the outbound buffer depth, and this runner's
 * own capacities/pause state. All off `GET /api/dashboard`'s `runner` section
 * — the runner's *own* facts about its hub link, not a live hub read; the
 * board link is the one hand-off to the hub app, minted from the endpoint the
 * wire now carries.
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
  imports: [KitAsyncState, KitFactList],
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

  /** The hub-link facts table's rows — a method, not a stored computed, since the
   * endpoint/link/loop rows need the `<ng-template>`s the view declares for them
   * (`KitFactList`'s own templated-row contract). */
  protected factRows(
    v: runnerApi.RunnerStatusView,
    endpointValue: TemplateRef<unknown>,
    linkValue: TemplateRef<unknown>,
    loopValue: TemplateRef<unknown>,
  ): readonly KitFact[] {
    return [
      { label: 'endpoint', template: endpointValue },
      { label: 'link', template: linkValue },
      { label: 'last flush', value: this.lastFlushLabel(), testid: 'hub-last-flush' },
      { label: 'buffered', value: `${v.hub.buffer_depth} events`, testid: 'hub-buffered' },
      { label: 'agents', value: `${v.capacities.used}/${v.capacities.max_agents} slots` },
      { label: 'loop', template: loopValue },
    ];
  }
}
