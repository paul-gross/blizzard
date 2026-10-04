import { ChangeDetectionStrategy, Component, computed, signal } from '@angular/core';
import { errorMessage, injectPendingMutationVariables } from 'fleet';
import { hasPermission, injectMeQuery } from '../core/auth/me.query';
import { injectRunnerPauseMutation, type RunnerPauseVars } from './runners.mutations';
import { injectRunnerRows, type RunnerRow } from './runner-rows';
import { pendingRunnerIds, withPendingRunnerPauses } from './runner-pause-override.model';
import { runnerPauseMutationKey } from '../core/mutation-keys';

import { FleetView } from './fleet-view';

/**
 * The `/fleet` route — the hub's mobile Fleet tab. Mounted only at mobile
 * widths (`app.routes.ts`'s `canMatch` guard); a desktop-width hit on this
 * path redirects to `/board`.
 *
 * A container (`bzh:frontend-container-presentational`): it folds the
 * registry + chunks reads via {@link injectRunnerRows}, owns the pause
 * mutation and the `runner:pause` permission read, and forwards both to the
 * presentational {@link FleetView}, which owns the phone-width markup. The
 * pending/error handling here mirrors `RunnerPanel` — the desktop half of the
 * same brake (`bzh:frontend-pending-override`).
 */
@Component({
  selector: 'app-fleet-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetView],
  templateUrl: './fleet-page.html',
  // A routed page fills the router outlet area, same as `GlanceBoard`'s own
  // `:host` — `FleetView`'s `:host { height: 100% }` needs this to resolve
  // against.
  styleUrl: './fleet-page.css',
})
export class FleetPage {
  private readonly pauseMutation = injectRunnerPauseMutation();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may operate the hub pause/resume brake
   * (`runner:pause`, admin-tier). */
  protected readonly canPause = computed(() => hasPermission(this.meQuery.data(), 'runner:pause'));

  /** Whether retired runners are listed — off by default; the chip in {@link FleetView} flips it. */
  protected readonly showRetired = signal(false);

  /** The registry rows and their async state, from the shared {@link injectRunnerRows} fold. */
  private readonly runnerRows = injectRunnerRows(() => this.showRetired());
  protected readonly state = this.runnerRows.state;

  /** Every runner id the shared `pauseMutation` is currently in flight for — one
   * mutation instance fires once per toggled row, so this is scoped by `mutationKey`
   * + variables rather than the mutation's bare `isPending()`, which would read
   * `true` for every row while any one of them is pausing/resuming (mirrors
   * `RunnerPanel`'s own `pendingPauses`). */
  private readonly pendingPauses = injectPendingMutationVariables<RunnerPauseVars>(runnerPauseMutationKey);

  /** {@link pendingPauses}, as the bare runner ids {@link FleetView} checks each row
   * against — the per-row disable that keeps a sibling row's toggle enabled while
   * only the one tapped disables. */
  protected readonly pendingRunnerIds = computed<readonly string[]>(() =>
    pendingRunnerIds(this.pendingPauses()),
  );

  /**
   * {@link injectRunnerRows}'s own rows, with a currently-pending pause/resume
   * mutation's own requested `hub_paused` folded onto its row
   * (`bzh:frontend-pending-override`) — mirrors `RunnerPanel`'s own `rows`. Purely
   * computed off {@link pendingPauses}' own variables, never a cache write — a
   * rejected pause/resume reverts to the real `hub_paused` for free the instant
   * `isPending()` clears.
   */
  protected readonly rows = computed<readonly RunnerRow[]>(() =>
    withPendingRunnerPauses(this.runnerRows.rows(), this.pendingPauses()),
  );

  /** The page's last pause/resume failure, or `null` ("report, don't
   * swallow") — reset at the start of every new attempt. */
  protected readonly actionError = signal<string | null>(null);

  protected toggleShowRetired(): void {
    this.showRetired.update((shown) => !shown);
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
