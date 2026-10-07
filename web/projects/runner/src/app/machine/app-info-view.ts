import { ChangeDetectionStrategy, Component, TemplateRef, input } from '@angular/core';
import { type KitFact, KitFactList, STATUS_TONE, type runnerApi, toneColor } from 'fleet';

/**
 * {@link LocalInfo}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the hub-link facts template — the
 * container keeps the query, the resolved async-state triad, the fleet-strip latch,
 * and the ticking clock {@link lastFlushLabel}/{@link lastTickLabel} are derived from.
 */
@Component({
  selector: 'app-info-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitFactList],
  templateUrl: './app-info-view.html',
  styleUrl: './app-info-view.css',
})
export class LocalInfoView {
  /** The runner's own hub-link facts. */
  readonly view = input.required<runnerApi.RunnerStatusView>();

  /** The last-known fleet counts, or `null` before the first successful read. */
  readonly fleet = input<runnerApi.FleetSummaryView | null>(null);

  /** The current read's `fleet_summary` slot is `null` (hub unreachable / not
   * wired, or no read has resolved yet) — the strip degrades to its dimmed
   * last-known state. The rest of the panel is hub-free, so it is unaffected. */
  readonly fleetStale = input(false);

  /** `-34s` since the last successful PULL, or `never` before first contact. */
  readonly lastFlushLabel = input.required<string>();

  readonly lastTickLabel = input.required<string>();

  /** Each fleet-count bucket's colour, derived from the chunk status it counts
   * (`FleetSummaryView`'s buckets: `ready`, `running`+`delivering`,
   * `waiting_on_human`+`paused`, `needs_human`) through `STATUS_TONE` and
   * `toneColor` — never a ladder of this panel's own (`bzh:frontend-formatters`). */
  protected readonly bucketColor = {
    ready: toneColor(STATUS_TONE.ready),
    running: toneColor(STATUS_TONE.running),
    waiting: toneColor(STATUS_TONE.waiting_on_human),
    needs: toneColor(STATUS_TONE.needs_human),
  } as const;

  /** The hub-link facts table's rows — a method, not a stored computed, since the
   * identity/endpoint/link/loop rows need the `<ng-template>`s this template declares for them
   * (`KitFactList`'s own templated-row contract). */
  protected factRows(
    identityValue: TemplateRef<unknown>,
    endpointValue: TemplateRef<unknown>,
    linkValue: TemplateRef<unknown>,
    loopValue: TemplateRef<unknown>,
  ): readonly KitFact[] {
    const v = this.view();
    return [
      { label: 'runner', template: identityValue, testid: 'runner-identity' },
      { label: 'endpoint', template: endpointValue },
      { label: 'link', template: linkValue },
      { label: 'last flush', value: this.lastFlushLabel(), testid: 'hub-last-flush' },
      { label: 'buffered', value: `${v.hub.buffer_depth} events`, testid: 'hub-buffered' },
      { label: 'agents', value: `${v.capacities.used}/${v.capacities.max_agents} slots` },
      { label: 'gates', value: v.gates?.join(', ') || 'none', testid: 'hub-gates' },
      { label: 'loop', template: loopValue },
    ];
  }
}
