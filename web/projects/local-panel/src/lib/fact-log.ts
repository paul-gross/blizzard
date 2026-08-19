import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import {
  compactRef,
  formatAbsolute,
  formatLocalClockWithDay,
  KitAsyncState,
  type KitAsyncStateValue,
  type LocalClockWithDay,
  type runnerApi,
} from 'fleet';

import { injectRunnerDashboardQuery } from './status.query';

/**
 * The local fact log — "runner store": the newest hub-bound facts off the
 * outbound ledger (`GET /api/facts`), newest first. Each row is the fact's
 * kind plus its correlated chunk/lease compact refs and a flush marker —
 * `✓` once the hub acked the seq, `·` while still buffered. A read of the
 * store's own ledger, not a synthesized feed.
 */
@Component({
  selector: 'local-fact-log',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  templateUrl: './fact-log.html',
  styleUrl: './fact-log.css',
})
export class FactLog {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly facts = computed(() => this.query.data()?.facts?.items ?? []);

  /** The async triad's resolved state — loading/error take precedence, then
   * an empty ledger, else the fact rows render. */
  protected readonly triadState = computed<KitAsyncStateValue>(() => {
    if (this.query.isPending()) return 'loading';
    if (this.query.isError()) return 'error';
    return this.facts().length === 0 ? 'empty' : 'ready';
  });

  protected ref(id: string): string {
    return compactRef(id);
  }

  /** The fact's browser-local clock time, plus day context when it's not from
   * today — the ledger reads as a tail -f, but an operator can be anywhere. */
  protected clockInfo(fact: runnerApi.FactView): LocalClockWithDay | null {
    return formatLocalClockWithDay(fact.created_at);
  }

  /** {@link clockInfo}'s full local date + time, for the stamp's hover tooltip
   * (issue #175). */
  protected absolute(fact: runnerApi.FactView): string {
    return formatAbsolute(fact.created_at);
  }
}
