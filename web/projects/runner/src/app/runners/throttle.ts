import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { asyncState, errorMessage, type runnerApi } from 'fleet';

import { injectHostRunnersQuery, injectHostSubscriptionsQuery } from '../core/host.query';
import { injectFloorClearMutation, injectRunnerDashboardQuery } from '../core/status.query';
import { floorClearScope } from './throttle.model';
import { ThrottleView } from './throttle-view';

@Component({
  selector: 'app-local-throttle',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ThrottleView],
  templateUrl: './throttle.html',
})
export class LocalThrottle {
  readonly runnerId = input.required<string>();
  private readonly dashboard = injectRunnerDashboardQuery(() => this.runnerId());
  private readonly runners = injectHostRunnersQuery();
  private readonly subscriptions = injectHostSubscriptionsQuery();
  protected readonly mutation = injectFloorClearMutation(() => this.runnerId());
  protected readonly throttle = computed<runnerApi.ThrottleStatusView | null>(() => this.dashboard.data()?.runner?.throttle ?? null);
  protected readonly state = computed(() => asyncState(this.dashboard, this.throttle() === null));
  protected readonly scope = computed(() => floorClearScope(
    this.throttle()?.floor.subscription ?? null,
    this.runnerId(),
    this.runners.data() ?? [],
    this.subscriptions.data() ?? [],
  ));
  protected readonly scopeReady = computed(() => !this.runners.isPending() && !this.runners.isError()
    && (this.throttle()?.floor.subscription === null || (!this.subscriptions.isPending() && !this.subscriptions.isError()))
    && this.scope() !== null);
  protected readonly error = signal<string | null>(null);

  protected clear(): void {
    const floor = this.throttle()?.floor;
    if (!floor || floor.admits || !this.scopeReady() || this.mutation.isPending()) return;
    this.error.set(null);
    this.mutation.mutate(
      { runnerId: this.runnerId(), subscription: floor.subscription },
      { onError: (error) => this.error.set(errorMessage(error, 'Clear floor failed.')) },
    );
  }
}
