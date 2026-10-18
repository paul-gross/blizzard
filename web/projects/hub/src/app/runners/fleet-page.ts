import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

import { errorMessage, injectPendingMutationVariables } from 'fleet';
import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import { runnerPauseMutationKey } from '../core/mutation-keys';
import { ProjectLens } from '../core/project-lens';
import { injectChildRouteParam } from '../core/route-state';
import { firstFleetId, fleetList, resolveSelection } from './fleet-page.model';
import { FleetView } from './fleet-view';
import { groupHostRows } from './host-rows.model';
import { injectHostVerbs } from './host-verbs';
import type { HostVerbEvent } from './host-verbs.model';
import { pendingRunnerIds, withPendingRunnerPauses } from './runner-pause-override.model';
import { injectRunnerRows } from './runner-rows';
import { type RunnerRow } from './runner-rows.model';
import { injectRunnerPauseMutation, type RunnerPauseVars } from './runners.mutations';

/**
 * The desktop `/fleet` route — host cards beside the selected host's detail. Selection is
 * the URL's `:id` child (a host id or a hostless runner id), so a selection is a deep link
 * and the list stays mounted across picks. A bare `/fleet` opens the first card.
 *
 * A container (`bzh:frontend-container-presentational`): it composes {@link injectRunnerRows},
 * the `runner:pause` permission and mutation (pending shown through the mutation variables,
 * `bzh:frontend-pending-override`) and the host verbs, and leaves every fold and branch to
 * `fleet-page.model.ts`.
 */
@Component({
  selector: 'app-fleet-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetView],
  templateUrl: './fleet-page.html',
  styleUrl: './fleet-page.css',
})
export class FleetPage {
  private readonly router = inject(Router);
  private readonly lens = inject(ProjectLens);
  private readonly meQuery = injectMeQuery();
  private readonly pauseMutation = injectRunnerPauseMutation();

  protected readonly canPause = computed(() => hasPermission(this.meQuery.data(), 'runner:pause'));
  protected readonly selectedId = injectChildRouteParam('id');

  private readonly runnerRows = injectRunnerRows();
  protected readonly state = this.runnerRows.state;

  private readonly pendingPauses = injectPendingMutationVariables<RunnerPauseVars>(runnerPauseMutationKey);
  protected readonly pendingRunnerIds = computed(() => pendingRunnerIds(this.pendingPauses()));

  private readonly groups = computed(() =>
    groupHostRows(withPendingRunnerPauses(this.runnerRows.rows(), this.pendingPauses()), this.runnerRows.cards()),
  );
  protected readonly list = computed(() => fleetList(this.groups()));
  protected readonly selection = computed(() => resolveSelection(this.groups(), this.selectedId()));

  protected readonly actionError = signal<string | null>(null);
  private readonly hostVerbs = injectHostVerbs((message) => this.actionError.set(message));
  protected readonly hostVerbsVm = this.hostVerbs.vm;

  constructor() {
    // A bare Fleet tab opens a card rather than an empty detail, once the reads settle.
    effect(() => {
      if (this.selectedId() !== null || this.state() !== 'ready') return;
      const open = firstFleetId(this.list());
      if (open !== null) void this.router.navigate(this.lens.link(['/fleet', open]), { replaceUrl: true });
    });
  }

  protected onPick(id: string): void {
    void this.router.navigate(this.lens.link(['/fleet', id]));
  }

  protected runHostVerb(event: HostVerbEvent): void {
    this.hostVerbs.run(event);
  }

  protected toggle(row: RunnerRow): void {
    this.actionError.set(null);
    this.pauseMutation.mutate(
      { runnerId: row.runner_id, paused: !row.hub_paused },
      {
        onError: (error) =>
          this.actionError.set(errorMessage(error, row.hub_paused ? 'Resume failed.' : 'Pause failed.')),
      },
    );
  }
}
