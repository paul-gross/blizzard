import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { hasPermission, injectMeQuery, injectRunnerPauseMutation, injectRunnerRows, type RunnerRow } from 'fleet';

import { FleetView } from './fleet-view';

/**
 * The `/fleet` route — the hub's mobile Fleet tab. Mounted only at mobile
 * widths (`app.routes.ts`'s `canMatch` guard); a desktop-width hit on this
 * path redirects to `/board`.
 *
 * A container (`bzh:frontend-container-presentational`): it folds the
 * registry + chunks reads via {@link injectRunnerRows}, owns the pause
 * mutation and the `runner:pause` permission read, and forwards both to the
 * presentational {@link FleetView}, which owns the phone-width markup.
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
  private readonly runnerRows = injectRunnerRows();

  protected readonly rows = this.runnerRows.rows;
  protected readonly state = this.runnerRows.state;

  /** Whether the current identity may operate the hub pause/resume brake
   * (`runner:pause`, admin-tier). */
  protected readonly canPause = computed(() => hasPermission(this.meQuery.data(), 'runner:pause'));

  protected toggle(row: RunnerRow): void {
    this.pauseMutation.mutate({ runnerId: row.runner_id, paused: !row.hub_paused });
  }
}
