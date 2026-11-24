import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { asyncState, errorMessage } from 'fleet';

import { injectHostRunnerDomainsQuery, injectHostSubscriptionsQuery } from '../core/host.query';
import { injectFloorClearMutation, injectRunnerDashboardStatusQuery } from '../core/status.query';
import type { Throttle } from '../core/throttle.domain';
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
  private readonly dashboard = injectRunnerDashboardStatusQuery(() => this.runnerId());
  private readonly runners = injectHostRunnerDomainsQuery();
  private readonly subscriptions = injectHostSubscriptionsQuery();
  protected readonly mutation = injectFloorClearMutation(() => this.runnerId());
  protected readonly throttle = computed<Throttle | null>(() => this.dashboard.data()?.runner?.throttle ?? null);
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
