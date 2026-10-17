import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { RouterLink } from '@angular/router';

import { KitAsyncState, type KitAsyncStateValue, KitBeacon, KitGlyph, KitPanel, KitSlotBar, RunnerConnection } from 'fleet';
import { LensLink } from 'fleet/shell';
import { groupHostRows, type HostCard } from './host-rows.model';
import { railVm } from './runner-rail.model';
import { SubscriptionBlocks } from './subscription-blocks';
import { localPauseHint, runnerSeenLabel, runnerSeenTag, type RunnerRow } from './runner-rows';

/**
 * The board rail's runner panel — the presentational half, most important first: a pace line per
 * distinct subscription, then one compact row per runner (its light, name, pause glyphs, host and
 * last contact; its agents per project), each name a link to the Fleet tab, which owns every
 * detail and action, then an environment strip per pool. Renders exactly the rows it is handed;
 * injects no query or mutation, so a spec drives it with plain inputs.
 */
@Component({
  selector: 'app-runner-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBeacon, KitGlyph, KitPanel, KitSlotBar, LensLink, RouterLink, SubscriptionBlocks],
  templateUrl: './runner-view.html',
  styleUrl: './runner-view.css',
})
export class RunnerPanelView {
  /** The registry rows to render — each runner plus its pre-folded claims. */
  readonly rows = input.required<readonly RunnerRow[]>();

  /** The host cards the rows group under; each of their workspaces draws an environment strip, and
   * their subscriptions the pace lines. */
  readonly cards = input<readonly HostCard[]>([]);

  /** The registry query's async state — loading/error withhold the empty copy until the read resolves. */
  readonly state = input.required<KitAsyncStateValue>();

  protected readonly rail = computed(() => railVm(groupHostRows(this.rows(), this.cards())));

  protected readonly localPauseHint = localPauseHint;

  protected readonly seenLabel = runnerSeenLabel;

  protected readonly seenTag = runnerSeenTag;

  /** The wire's connection vocabulary, for the row's connection classes. */
  protected readonly connection = RunnerConnection;
}
