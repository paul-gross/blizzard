import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { compactRef, type runnerApi } from 'fleet';

import { ageMs, formatHeldFor } from './age';
import { injectRunnerAsksQuery } from './status.query';

/**
 * The local-asks panel — "answers live at the hub": every ask still open on
 * this machine, with the chunk it parks and the question text. The answer verb
 * is a hub write (`blizzard hub answer` or the fleet board), so this panel is
 * read-only by design — it surfaces the wait, it never answers.
 */
@Component({
  selector: 'fleet-local-asks',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap" data-testid="local-asks">
      @if (query.isPending()) {
        <p class="status">LOADING…</p>
      } @else if (query.isError()) {
        <p class="status error">ASKS UNAVAILABLE</p>
      } @else if (asks().length === 0) {
        <p class="status" data-testid="asks-empty">NO OPEN ASKS ON THIS MACHINE</p>
      } @else {
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
      }
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
    .status {
      position: absolute;
      left: 50%;
      top: 50%;
      transform: translate(-50%, -50%);
      white-space: nowrap;
      color: var(--label-dim);
      font-size: var(--fs-xs);
      letter-spacing: 0.12em;
    }
    .status.error {
      color: var(--red);
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

  protected chunkRef(ask: runnerApi.AskView): string {
    return compactRef(ask.chunk_id);
  }

  protected askedFor(ask: runnerApi.AskView): string {
    const age = ageMs(ask.asked_at, Date.now());
    return age === null ? '—' : formatHeldFor(age);
  }
}
