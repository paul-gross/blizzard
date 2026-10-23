import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { errorMessage, injectPendingMutationVariables, KitBackBar } from 'fleet';
import { LensLink } from 'fleet/shell';
import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import { runnerPauseMutationKey } from '../core/mutation-keys';
import { injectChildRouteParam } from '../core/route-state';
import { resolveSelection } from './fleet-page.model';
import { HostDetail } from './host-detail';
import { groupHostRows } from './host-rows.model';
import { HostlessRunnerDetail } from './hostless-runner-detail';
import { pendingRunnerIds, withPendingRunnerPauses } from './runner-pause-override.model';
import { injectRunnerRows } from './runner-rows';
import { type RunnerRow } from './runner-rows.model';
import { RunnerPanelView } from './runner-view';
import { injectRunnerPauseMutation, type RunnerPauseVars } from './runners.mutations';

/**
 * The `/fleet` route — the hub's mobile Fleet tab: the fleet at a glance, each runner with a
 * full-width pause/resume toggle, and, under `/fleet/:id`, one host's (or hostless runner's)
 * full breakdown behind a back bar.
 *
 * A container (`bzh:frontend-container-presentational`): it folds the registry + chunks reads
 * via {@link injectRunnerRows}, owns the pause mutation and the `runner:pause` permission read,
 * and hands both to the board rail's own {@link RunnerPanelView} and the desktop Fleet tab's
 * detail views. The host verbs — enroll, revoke, retire — stay on the desktop Fleet tab.
 * Pending pauses show through the mutation variables (`bzh:frontend-pending-override`), as
 * `FleetPage` does.
 */
@Component({
  selector: 'app-runners-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HostDetail, HostlessRunnerDetail, KitBackBar, LensLink, RouterLink, RunnerPanelView],
  templateUrl: './runners-page.html',
  styleUrl: './runners-page.css',
})
export class RunnersPage {
  private readonly pauseMutation = injectRunnerPauseMutation();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may operate the hub pause/resume brake
   * (`runner:pause`, admin-tier). */
  protected readonly canPause = computed(() => hasPermission(this.meQuery.data(), 'runner:pause'));

  /** The host or hostless runner id the URL opens, or `null` for the overview. */
  protected readonly selectedId = injectChildRouteParam('id');

  private readonly runnerRows = injectRunnerRows();
  protected readonly state = this.runnerRows.state;
  protected readonly cards = this.runnerRows.cards;

  /** Every runner id the shared `pauseMutation` is in flight for, scoped by `mutationKey` +
   * variables so only the tapped runner's toggle disables. */
  private readonly pendingPauses = injectPendingMutationVariables<RunnerPauseVars>(runnerPauseMutationKey);

  protected readonly pendingRunnerIds = computed<readonly string[]>(() => pendingRunnerIds(this.pendingPauses()));

  /** The registry rows with each pending pause/resume's requested `hub_paused` folded on — a
   * rejected request reverts to the real flag the instant it settles. */
  protected readonly rows = computed<readonly RunnerRow[]>(() =>
    withPendingRunnerPauses(this.runnerRows.rows(), this.pendingPauses()),
  );

  /** The opened host or hostless runner — the desktop Fleet tab's own resolution of `:id`. */
  protected readonly selection = computed(() => resolveSelection(groupHostRows(this.rows(), this.cards()), this.selectedId()));

  /** The page's last pause/resume failure, or `null` ("report, don't swallow") — reset at the
   * start of every new attempt. */
  protected readonly actionError = signal<string | null>(null);

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
