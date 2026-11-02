import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { asyncState, errorMessage, type runnerApi } from 'fleet';

import { injectRunnerDashboardQuery } from '../core/status.query';
import { selftestRows, startingHarness } from './selftest.model';
import { injectSelftestStartMutation } from './selftest.mutations';
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
  private readonly mutation = injectSelftestStartMutation(() => this.runnerId());
  private readonly started = signal<ReadonlyMap<string, runnerApi.SelfTestView>>(new Map());
  private readonly startErrors = signal<ReadonlyMap<string, string>>(new Map());
  private readonly reads = injectSelftestRunsQuery(
    () => this.runnerId(),
    () => [...this.started().values()].map((run) => ({ harness: run.harness, id: run.id })),
  );
  private readonly health = computed(() => this.dashboard.data()?.harness_health.items ?? []);
  protected readonly state = computed(() => asyncState(this.dashboard, this.health().length === 0));
  private readonly starting = computed(() => startingHarness(this.mutation.isPending(), this.mutation.variables()?.harness));
  protected readonly rows = computed(() =>
    selftestRows(this.health(), this.started(), this.reads(), this.starting(), this.startErrors()));

  protected run(harness: string): void {
    if (this.rows().find((row) => row.harness === harness)?.busy) return;
    this.startErrors.update((errors) => withoutKey(errors, harness));
    this.mutation.mutate(
      { runnerId: this.runnerId(), harness },
      {
        onSuccess: (run) => this.started.update((runs) => new Map(runs).set(harness, run)),
        onError: (error) => this.startErrors.update((errors) =>
          new Map(errors).set(harness, errorMessage(error, 'Selftest failed to start.'))),
      },
    );
  }
}

function withoutKey<V>(map: ReadonlyMap<string, V>, key: string): ReadonlyMap<string, V> {
  const next = new Map(map);
  next.delete(key);
  return next;
}
