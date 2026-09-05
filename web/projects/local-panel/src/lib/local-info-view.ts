import { ChangeDetectionStrategy, Component, TemplateRef, input } from '@angular/core';
import { type KitFact, KitFactList, type runnerApi } from 'fleet';

/**
 * {@link LocalInfo}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the hub-link facts template — the
 * container keeps the query, the resolved async-state triad, the fleet-strip latch,
 * and the ticking clock {@link lastFlushLabel}/{@link lastTickLabel} are derived from.
 */
@Component({
  selector: 'local-info-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitFactList],
  templateUrl: './local-info-view.html',
  styleUrl: './local-info-view.css',
})
export class LocalInfoView {
  /** The runner's own hub-link facts — resolved and null-guarded by the container. */
  readonly view = input.required<runnerApi.RunnerStatusView>();

  /** The last-known fleet counts, or `null` before the first successful read —
   * the container's own latch, not TanStack's `data()`. */
  readonly fleet = input<runnerApi.FleetSummaryView | null>(null);

  /** The current read's `fleet_summary` slot is `null` (hub unreachable / not
   * wired, or no read has resolved yet) — the strip degrades to its dimmed
   * last-known state. The rest of the panel is hub-free, so it is unaffected. */
  readonly fleetStale = input(false);

  /** `-34s` since the last successful PULL, or `never` before first contact —
   * the container's own ticking clock. */
  readonly lastFlushLabel = input.required<string>();

  readonly lastTickLabel = input.required<string>();

  /** The hub-link facts table's rows — a method, not a stored computed, since the
   * endpoint/link/loop rows need the `<ng-template>`s this template declares for them
   * (`KitFactList`'s own templated-row contract). */
  protected factRows(
    endpointValue: TemplateRef<unknown>,
    linkValue: TemplateRef<unknown>,
    loopValue: TemplateRef<unknown>,
  ): readonly KitFact[] {
    const v = this.view();
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
