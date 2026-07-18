import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { ageMs, compactRef, formatHeldFor, KitAsyncState, type KitAsyncStateValue, type runnerApi } from 'fleet';

import { injectRunnerAsksQuery } from './status.query';

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
  template: `
    <div class="wrap" data-testid="local-asks">
      <fleet-kit-async-state
        [state]="triadState()"
        loadingText="LOADING…"
        errorText="ASKS UNAVAILABLE"
        emptyText="NO OPEN ASKS ON THIS MACHINE"
        emptyTestid="asks-empty"
      >
        @for (ask of asks(); track ask.question_id) {
          <div class="ask" data-testid="ask-row" [attr.data-question-id]="ask.question_id">
            <div class="a-hdr">
              <span class="chunk">{{ chunkRef(ask) }}</span>
              <span class="asked">asked {{ askedFor(ask) }} ago</span>
            </div>
            <div class="q">{{ ask.question }}</div>
            <div class="route">answer is a hub write → <code>blizzard hub answer</code> or the fleet board</div>
          </div>
        }
      </fleet-kit-async-state>
    </div>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .wrap {
      position: relative;
      min-height: 40px;
    }
    .ask {
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
    }
    .a-hdr {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 8px;
    }
    .chunk {
      color: var(--amber-hi);
      font-size: var(--fs-sm);
    }
    .asked {
      color: var(--label);
      font-size: var(--fs-label);
    }
    .q {
      color: var(--text);
      font-size: var(--fs-sm);
      margin-top: 2px;
    }
    .route {
      color: var(--label-dim);
      font-size: var(--fs-label);
      margin-top: 3px;
    }
    .route code {
      color: var(--label);
    }
  `,
})
export class LocalAsks {
  protected readonly query = injectRunnerAsksQuery();

  protected readonly asks = computed(() => this.query.data() ?? []);

  /** The async triad's resolved state — loading/error take precedence, then
   * no open asks, else the ask rows render. */
  protected readonly triadState = computed<KitAsyncStateValue>(() => {
    if (this.query.isPending()) return 'loading';
    if (this.query.isError()) return 'error';
    return this.asks().length === 0 ? 'empty' : 'ready';
  });

  protected chunkRef(ask: runnerApi.AskView): string {
    return compactRef(ask.chunk_id);
  }

  protected askedFor(ask: runnerApi.AskView): string {
    const age = ageMs(ask.asked_at, Date.now());
    return age === null ? '—' : formatHeldFor(age);
  }
}
