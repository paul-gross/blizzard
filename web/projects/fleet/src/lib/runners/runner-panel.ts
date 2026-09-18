import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';

import type { RunnerView } from '../api/hub';
import { hasPermission, injectMeQuery } from '../auth/me.query';
import { errorMessage } from '../error-message';
import { runnerPauseMutationKey } from '../mutation-keys';
import { injectPendingMutationVariables, isPendingFor } from '../mutation-pending';
import { RunnerPanelView } from './runner-view';
import { injectRunnerPauseMutation, type RunnerPauseVars } from './runners.mutations';
import { injectRunnerRows } from './runner-rows';

/**
 * The runner panel — the fleet registry in the board's right rail: each
 * registered runner with its derived **liveness** (`online` vs the
 * staleness threshold), last-seen time, and **paused** state, plus a pause/resume
 * toggle — the operator's brake, declarative state the runner reads on its
 * outbound pull.
 *
 * A container: it folds the registry + chunks reads via {@link injectRunnerRows},
 * owns the pause mutation, and renders the presentational {@link RunnerPanelView}.
 * The live-update service re-reads on `runner-changed`.
 */
@Component({
  selector: 'fleet-runner-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RunnerPanelView],
  templateUrl: './runner-panel.html',
})
export class RunnerPanel {
  private readonly pauseMutation = injectRunnerPauseMutation();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may operate the hub pause/resume brake
   * (`runner:pause`, admin-tier — issue #93). Passed to the presentational view, which
   * withholds the toggle button when false so a `contributor` never sees a control that
   * would 403. `null`/pending resolves to `false` (hidden until confirmed). */
  protected readonly canPause = computed(() => hasPermission(this.meQuery.data(), 'runner:pause'));

  /** The registry rows and their async state, from the shared {@link injectRunnerRows} fold. */
  private readonly runnerRows = injectRunnerRows();
  protected readonly rows = this.runnerRows.rows;
  protected readonly state = this.runnerRows.state;

  /** Every runner id the shared `pauseMutation` is currently in flight for — one
   * mutation instance fires once per toggled row, so this is scoped by `mutationKey`
   * + variables rather than the mutation's bare `isPending()`, which would read
   * `true` for every row while any one of them is pausing/resuming. */
  private readonly pendingPauses = injectPendingMutationVariables<RunnerPauseVars>(runnerPauseMutationKey);

  /** Whether `runnerId`'s own hub pause/resume mutation is in flight. */
  protected readonly isPausePending = (runnerId: string): boolean =>
    isPendingFor(this.pendingPauses(), (vars) => vars.runnerId === runnerId);

  /** {@link pendingPauses}, as the bare runner ids {@link RunnerPanelView} checks
   * each row against — the per-row disable that keeps a sibling row's toggle
   * enabled while only the one clicked disables. Plain data rather than
   * {@link isPausePending} itself threaded down, since every input on that view
   * is a value, never a callback. */
  protected readonly pendingRunnerIds = computed<readonly string[]>(() =>
    this.pendingPauses().map((vars) => vars.runnerId),
  );

  /** The panel's last pause/resume failure, or `null` (issue #42's "report, don't
   * swallow") — reset at the start of every new attempt. */
  protected readonly actionError = signal<string | null>(null);

  protected toggle(runner: RunnerView): void {
    this.actionError.set(null);
    this.pauseMutation.mutate(
      { runnerId: runner.runner_id, paused: !runner.hub_paused },
      {
        onError: (error) =>
          this.actionError.set(errorMessage(error, runner.hub_paused ? 'Resume failed.' : 'Pause failed.')),
      },
    );
  }
}
