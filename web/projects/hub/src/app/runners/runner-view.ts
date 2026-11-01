import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import { KitAsyncState, type KitAsyncStateValue, KitButton, KitGlyph, KitPanel, KitSlotBar, RunnerConnection, SubscriptionBlocks, KitErrorLine } from 'fleet';
import { LensLink } from 'fleet/shell';
import { groupHostRows, type HostCard } from './host-rows.model';
import { railVm } from './runner-rail.model';
import { localPauseHint, runnerSeenLabel, runnerSeenTag, runnerToggleHint, type RunnerRow } from './runner-rows.model';
import { StatusLight } from './status-light';

/**
 * The board rail's runner panel — the presentational half, most important first: a pace line per
 * distinct subscription, then one compact row per runner (its light, name, pause glyphs, host and
 * last contact; its agents per project), each name a link to the Fleet tab, which owns every
 * detail and action, then an environment strip per pool. Where its host grants the hub brake
 * (`canPause`, the phone's Fleet tab), each runner also carries a full-width pause/resume
 * toggle. Renders exactly the rows it is handed; injects no query or mutation, so a spec drives
 * it with plain inputs.
 */
@Component({
  selector: 'app-runner-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton, KitGlyph, KitPanel, KitSlotBar, LensLink, RouterLink, StatusLight, SubscriptionBlocks, KitErrorLine],
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

  /** Whether each runner carries the hub pause/resume toggle — withheld for an identity without
   * `runner:pause` (admin-tier), and on the board rail, which leaves every action to the Fleet tab. */
  readonly canPause = input(false);

  /** The runner ids with a pause/resume currently in flight — only those rows' toggles disable. */
  readonly pendingRunnerIds = input<readonly string[]>([]);

  /** The host's last pause/resume failure, or `null` — a visible notice above the runners. */
  readonly actionError = input<string | null>(null);

  /** Emitted with the row to flip the **hub** brake on. */
  readonly togglePause = output<RunnerRow>();

  protected readonly rail = computed(() => railVm(groupHostRows(this.rows(), this.cards())));

  protected readonly localPauseHint = localPauseHint;

  protected readonly toggleHint = runnerToggleHint;

  protected readonly seenLabel = runnerSeenLabel;

  protected readonly seenTag = runnerSeenTag;

  /** The wire's connection vocabulary, for the row's connection classes. */
  protected readonly connection = RunnerConnection;
}
