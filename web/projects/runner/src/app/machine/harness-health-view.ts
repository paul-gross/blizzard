import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { KitBadge, type runnerApi } from 'fleet';

/**
 * One row per configured harness binding: its id and version, whether it is available,
 * and — when it is not, or is degraded — the cause and the declared degradations, as the
 * runner computed them.
 *
 * Presentational only (`bzh:frontend-container-presentational`): {@link LocalHarnessHealth}
 * owns the read.
 */
@Component({
  selector: 'app-harness-health-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  templateUrl: './harness-health-view.html',
  styleUrl: './harness-health-view.css',
})
export class HarnessHealthView {
  readonly harnesses = input.required<readonly runnerApi.HarnessHealthView[]>();
}
