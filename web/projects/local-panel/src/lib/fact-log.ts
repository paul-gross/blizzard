import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, KitAsyncState } from 'fleet';

import { FactLogView } from './fact-log-view';
import { injectRunnerDashboardQuery } from './status.query';

/**
 * The local fact log **container** — "runner store": the newest hub-bound facts
 * off the outbound ledger (`GET /api/facts`), newest first. Owns the query and the
 * resolved async-state triad; the presentational {@link FactLogView} owns the row
 * template (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'local-fact-log',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, FactLogView],
  templateUrl: './fact-log.html',
  styleUrl: './fact-log.css',
})
export class FactLog {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly facts = computed(() => this.query.data()?.facts?.items ?? []);

  /** The async triad's resolved state — loading/error take precedence, then
   * an empty ledger, else the fact rows render. */
  protected readonly triadState = computed(() => asyncState(this.query, this.facts().length === 0));
}
