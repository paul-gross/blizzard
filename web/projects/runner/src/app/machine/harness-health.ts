import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, KitAsyncState } from 'fleet';

import { injectRunnerDashboardQuery } from '../core/status.query';
import { HarnessHealthView } from './harness-health-view';

/**
 * The harness-health panel **container** — owns the active runner's dashboard read's
 * `harness_health` section and its async-state triad; the presentational
 * {@link HarnessHealthView} owns the row template (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'app-harness-health',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HarnessHealthView, KitAsyncState],
  templateUrl: './harness-health.html',
  styleUrl: './harness-health.css',
})
export class LocalHarnessHealth {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly harnesses = computed(() => this.query.data()?.harness_health?.items ?? []);

  protected readonly triadState = computed(() => asyncState(this.query, this.harnesses().length === 0));
}
