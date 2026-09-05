import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { ageMs, asyncState, compactRef, formatHeldFor, injectNowSignal, KitAsyncState } from 'fleet';

import { type AskRow, LocalAsksView } from './local-asks-view';
import { injectRunnerDashboardQuery } from './status.query';

/**
 * The local-asks panel **container** — "answers live at the hub": every ask still
 * open on this machine, with the chunk it parks and the question text. Owns the
 * query, the resolved async-state triad, and the ticking clock
 * {@link AskRow.askedFor} is derived from; the presentational {@link LocalAsksView}
 * owns the row template (`bzh:frontend-container-presentational`). The answer verb
 * is a hub write (`blizzard hub answer` or the fleet board), so this panel is
 * read-only by design — it surfaces the wait, it never answers.
 */
@Component({
  selector: 'local-asks',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, LocalAsksView],
  templateUrl: './local-asks.html',
  styleUrl: './local-asks.css',
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

  private askedFor(askedAt: string): string {
    const age = ageMs(askedAt, this.now());
    return age === null ? '—' : formatHeldFor(age);
  }

  protected readonly rows = computed<readonly AskRow[]>(() =>
    this.asks().map((ask) => ({
      questionId: ask.question_id,
      chunkRef: compactRef(ask.chunk_id),
      askedFor: this.askedFor(ask.asked_at),
      question: ask.question,
    })),
  );
}
