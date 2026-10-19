import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, KitAsyncState } from 'fleet';

import { FactLogView } from './fact-log-view';
import { injectPanelFacts } from '../core/panel-reads';

/**
 * The fact log **container** — "runner store": the newest hub-bound facts off each
 * covered runner's outbound ledger (`GET /api/dashboard`), merged newest first and
 * narrowed by the filters ({@link injectPanelFacts}). Owns the query and the
 * resolved async-state triad; the presentational {@link FactLogView} owns the row
 * template (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'app-fact-log',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, FactLogView],
  templateUrl: './fact-log.html',
  styleUrl: './fact-log.css',
})
export class FactLog {
  protected readonly query = injectPanelFacts();

  protected readonly facts = this.query.data;

  /** The async triad's resolved state — loading/error take precedence, then
   * an empty ledger, else the fact rows render. */
  protected readonly triadState = computed(() => asyncState(this.query, this.facts().length === 0));
}
