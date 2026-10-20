import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { SubscriptionPaceGroup, WorkspaceTable } from 'fleet';
import { hostLight } from './connection-status.model';
import type { HostCard } from './host-rows.model';
import { HostVerbBar } from './host-verb-bar';
import { type HostVerbEvent, type HostVerbsVm, NO_HOST_VERBS } from './host-verbs.model';
import { StatusLight } from './status-light';

/**
 * One host's card on the runners view — its name and last report, its workspaces with
 * their environments in use, and each subscription once naming the runners that spend it.
 * The card's runner rows are projected in by the registry view rendering it, so the rail
 * and the phone each keep their own row markup under one card.
 *
 * It carries the host verbs — retire, revoke-token and enroll — through {@link HostVerbBar},
 * which owns their confirms and dialogs.
 *
 * Presentational only: the container folds the hosts read into {@link HostCard} and runs
 * each confirmed {@link HostVerbEvent}.
 */
@Component({
  selector: 'app-host-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HostVerbBar, StatusLight, SubscriptionPaceGroup, WorkspaceTable],
  templateUrl: './host-card.html',
  styleUrl: './host-card.css',
})
export class HostCardView {
  /** The host's own facts, pre-folded. */
  readonly card = input.required<HostCard>();

  /** The verbs' permission, pending state, refusal and enrolled token. */
  readonly verbs = input<HostVerbsVm>(NO_HOST_VERBS);

  /** A confirmed gesture on this card. */
  readonly verb = output<HostVerbEvent>();

  protected readonly light = computed(() => hostLight(this.card().online, this.card().connection));
}
