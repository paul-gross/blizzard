import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { ageMs, asyncState, compactRef, formatHeldFor, injectNowSignal, KitAsyncState, type runnerApi } from 'fleet';

import { injectRunnerDashboardQuery } from './status.query';

/**
 * The local-asks panel — "answers live at the hub": every ask still open on
 * this machine, with the chunk it parks and the question text. The answer verb
 * is a hub write (`blizzard hub answer` or the fleet board), so this panel is
 * read-only by design — it surfaces the wait, it never answers.
 */
@Component({
  selector: 'local-asks',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  templateUrl: './local-asks.html',
  styleUrl: './local-asks.css',
})
export class LocalAsks {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly asks = computed(() => this.query.data()?.asks?.items ?? []);

  /** The async triad's resolved state — loading/error take precedence, then
   * no open asks, else the ask rows render. */
  protected readonly triadState = computed(() => asyncState(this.query, this.asks().length === 0));

  /** Ticks once a second so {@link askedFor} advances between polls instead of
   * sitting frozen at whatever age the last read carried. */
  private readonly now = injectNowSignal(1000);

  protected chunkRef(ask: runnerApi.AskView): string {
    return compactRef(ask.chunk_id);
  }

  protected askedFor(ask: runnerApi.AskView): string {
    const age = ageMs(ask.asked_at, this.now());
    return age === null ? '—' : formatHeldFor(age);
  }
}
