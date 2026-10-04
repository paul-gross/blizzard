import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { asyncState, injectNowSignal, KitAsyncState } from 'fleet';

import { type AskRow, LocalAsksView } from './app-asks-view';
import { askRows } from './app-asks.model';
import { injectRunnerDashboardQuery } from '../core/status.query';

/**
 * The app-asks panel **container** — "answers live at the hub": every ask still
 * open on this machine, with the chunk it parks and the question text. Owns the
 * query, the resolved async-state triad, and the ticking clock
 * {@link AskRow.askedFor} is derived from; the presentational {@link LocalAsksView}
 * owns the row template (`bzh:frontend-container-presentational`). The answer verb
 * is a hub write (`blizzard hub question answer` or the fleet board), so this panel is
 * read-only by design — it surfaces the wait, it never answers.
 */
@Component({
  selector: 'app-asks',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, LocalAsksView],
  templateUrl: './app-asks.html',
  styleUrl: './app-asks.css',
})
export class LocalAsks {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly asks = computed(() => this.query.data()?.asks?.items ?? []);

  /** The async triad's resolved state — loading/error take precedence, then
   * no open asks, else the ask rows render. */
  protected readonly triadState = computed(() => asyncState(this.query, this.asks().length === 0));

  /** Ticks once a second so each row's `askedFor` advances between polls instead of
   * sitting frozen at whatever age the last read carried. */
  private readonly now = injectNowSignal(1000);

  protected readonly rows = computed<readonly AskRow[]>(() => askRows(this.asks(), this.now()));
}
