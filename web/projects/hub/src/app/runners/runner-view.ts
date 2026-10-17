import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { KitAsyncState, type KitAsyncStateValue, KitBadge, KitBeacon, KitPanel, KitSlotBar, RunnerConnection } from 'fleet';
import { LensLink } from 'fleet/shell';
import { groupHostRows, type HostCard } from './host-rows.model';
import { railVm } from './runner-rail.model';
import { localPauseHint, runnerSeenLabel, type RunnerRow } from './runner-rows';

/**
 * The board rail's runner panel — the presentational half: a fleet-wide environment strip, then
 * one compact row per runner (its light, name and last contact; its agents; its pause badges),
 * and a link to the Fleet tab, which owns every detail and action. Renders exactly the rows it
 * is handed; injects no query or mutation, so a spec drives it with plain inputs.
 */
@Component({
  selector: 'app-runner-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitBeacon, KitPanel, KitSlotBar, LensLink, RouterLink],
  templateUrl: './runner-view.html',
  styleUrl: './runner-view.css',
})
export class RunnerPanelView {
  /** The registry rows to render — each runner plus its pre-folded claims. */
  readonly rows = input.required<readonly RunnerRow[]>();

  /** The host cards the rows group under; their workspaces make up the environment strip. */
  readonly cards = input<readonly HostCard[]>([]);

  /** The registry query's async state — loading/error withhold the empty copy until the read resolves. */
  readonly state = input.required<KitAsyncStateValue>();

  protected readonly rail = computed(() => railVm(groupHostRows(this.rows(), this.cards())));

  protected readonly localPauseHint = localPauseHint;

  protected readonly seenLabel = runnerSeenLabel;

  /** The wire's connection vocabulary, for the row's connection classes and light. */
  protected readonly connection = RunnerConnection;
}
