import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import type { RunnerView } from '../api/hub';
import { hasPermission, injectMeQuery } from '../auth/me.query';
import { RunnerPanelView } from './runner-view';
import { injectRunnerPauseMutation } from './runners.mutations';
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

  protected toggle(runner: RunnerView): void {
    this.pauseMutation.mutate({ runnerId: runner.runner_id, paused: !runner.hub_paused });
  }
}
