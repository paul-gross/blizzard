import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { RunnerConnection, SubscriptionPaceGroup, WorkspaceTable } from 'fleet';
import type { HostCard } from './host-rows.model';

/**
 * One host's card on the runners view — its name and last report, its workspaces with
 * their environments in use, and each subscription once naming the runners that spend it.
 * The card's runner rows are projected in by the registry view rendering it, so the rail
 * and the phone each keep their own row markup under one card.
 *
 * Presentational only: the container folds the hosts read into {@link HostCard}.
 */
@Component({
  selector: 'app-host-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [SubscriptionPaceGroup, WorkspaceTable],
  templateUrl: './host-card.html',
  styleUrl: './host-card.css',
})
export class HostCardView {
  /** The host's own facts, pre-folded. */
  readonly card = input.required<HostCard>();

  /** The wire's connection vocabulary, for the host's liveness square. */
  protected readonly connection = RunnerConnection;
}
