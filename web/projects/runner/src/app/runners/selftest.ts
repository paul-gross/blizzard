import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { asyncState, errorMessage, injectPendingMutationVariables, type runnerApi } from 'fleet';

import { selftestStartMutationKey } from '../core/mutation-keys';
import { injectRunnerDashboardQuery } from '../core/status.query';
import { pendingHarnesses, selftestRows, selftestRunRefs } from './selftest.model';
import { injectSelftestStartMutation, type SelftestStartVars } from './selftest.mutations';
import { injectSelftestRunsQuery } from './selftest.query';
import { SelftestView } from './selftest-view';

/**
 * The machine page's Selftest panel (`bzh:frontend-container-presentational`): one Run per
 * harness in the dashboard's `harness_health`. Runs are unconfirmed and concurrent; the runner
 * keeps them in memory only, so this container holds each harness's latest run id.
 */
@Component({
  selector: 'app-local-selftest',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [SelftestView],
  templateUrl: './selftest.html',
})
export class LocalSelftest {
  readonly runnerId = input.required<string>();
  private readonly dashboard = injectRunnerDashboardQuery(() => this.runnerId());
  private readonly started = signal<ReadonlyMap<string, runnerApi.SelfTestView>>(new Map());
  private readonly startErrors = signal<ReadonlyMap<string, string>>(new Map());
  private readonly mutation = injectSelftestStartMutation(() => this.runnerId(), {
    onSuccess: (run, vars) => this.started.update((runs) => new Map(runs).set(vars.harness, run)),
    onError: (error, vars) => this.startErrors.update((errors) =>
      new Map(errors).set(vars.harness, errorMessage(error, 'Selftest failed to start.'))),
  });
  /** Every start pending for this runner (`bzh:frontend-pending-override`). */
  private readonly pendingStarts = injectPendingMutationVariables<SelftestStartVars>(() => selftestStartMutationKey(this.runnerId()));
  private readonly reads = injectSelftestRunsQuery(() => this.runnerId(), () => selftestRunRefs(this.started()));
  private readonly health = computed(() => this.dashboard.data()?.harness_health.items ?? []);
  protected readonly state = computed(() => asyncState(this.dashboard, this.health().length === 0));
  protected readonly rows = computed(() =>
    selftestRows(this.health(), this.started(), this.reads(), pendingHarnesses(this.pendingStarts()), this.startErrors()));

  protected run(harness: string): void {
    if (this.rows().find((row) => row.harness === harness)?.busy) return;
    this.startErrors.update((errors) => withoutKey(errors, harness));
    this.mutation.mutate({ runnerId: this.runnerId(), harness });
  }
}

function withoutKey<V>(map: ReadonlyMap<string, V>, key: string): ReadonlyMap<string, V> {
  const next = new Map(map);
  next.delete(key);
  return next;
}
