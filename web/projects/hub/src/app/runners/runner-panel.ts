import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';

import { type RunnerRegistryView, errorMessage, injectPendingMutationVariables } from 'fleet';
import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import { runnerPauseMutationKey } from '../core/mutation-keys';
import { RunnerPanelView } from './runner-view';
import { injectRunnerPauseMutation, type RunnerPauseVars } from './runners.mutations';
import { injectRunnerRows, type RunnerRow } from './runner-rows';
import { pendingRunnerIds, withPendingRunnerPauses } from './runner-pause-override.model';

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
  selector: 'app-runner-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RunnerPanelView],
  templateUrl: './runner-panel.html',
})
export class RunnerPanel {
  private readonly pauseMutation = injectRunnerPauseMutation();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may operate the hub pause/resume brake
   * (`runner:pause`, admin-tier), passed to the presentational view. `null`/pending resolves to `false` (hidden until confirmed). */
  protected readonly canPause = computed(() => hasPermission(this.meQuery.data(), 'runner:pause'));

  /** The registry rows and their async state, from the shared {@link injectRunnerRows} fold. */
  private readonly runnerRows = injectRunnerRows();
  protected readonly state = this.runnerRows.state;

  /** Every runner id the shared `pauseMutation` is currently in flight for — one
   * mutation instance fires once per toggled row, so this is scoped by `mutationKey`
   * + variables rather than the mutation's bare `isPending()`, which would read
   * `true` for every row while any one of them is pausing/resuming. */
  private readonly pendingPauses = injectPendingMutationVariables<RunnerPauseVars>(runnerPauseMutationKey);

  /** {@link pendingPauses}, as the bare runner ids {@link RunnerPanelView} checks
   * each row against — the per-row disable that keeps a sibling row's toggle
   * enabled while only the one clicked disables. Plain data rather than a
   * per-row predicate threaded down, since every input on that view is a
   * value, never a callback. */
  protected readonly pendingRunnerIds = computed<readonly string[]>(() =>
    pendingRunnerIds(this.pendingPauses()),
  );

  /**
   * {@link injectRunnerRows}'s own rows, with a currently-pending pause/resume
   * mutation's own requested `hub_paused` folded onto its row
   * (`bzh:frontend-pending-override`). The hub brake is a plain fact this exact
   * mutation sets directly — `domain/execution/pause.md`'s "Effective paused is the OR
   * of the two brakes, each cleared only where it was set" — not a value derived from
   * some other precedence ladder, so the override is total: the mutation's own
   * variables already name the requested value, no guessing required. Scoped by
   * `mutationKey` + variables the same way {@link pendingRunnerIds} is, so a sibling
   * row mid-toggle is untouched. Purely computed off {@link pendingPauses}' own
   * variables, never a cache write — fed to {@link RunnerPanelView} in
   * {@link injectRunnerRows}'s `rows` place, so a rejected pause/resume reverts to the
   * real `hub_paused` for free the instant `isPending()` clears.
   */
  protected readonly rows = computed<readonly RunnerRow[]>(() =>
    withPendingRunnerPauses(this.runnerRows.rows(), this.pendingPauses()),
  );

  /** The panel's last pause/resume failure, or `null` ("report, don't
   * swallow") — reset at the start of every new attempt. */
  protected readonly actionError = signal<string | null>(null);

  protected toggle(runner: RunnerRegistryView): void {
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
