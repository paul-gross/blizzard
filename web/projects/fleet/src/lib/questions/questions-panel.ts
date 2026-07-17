import { ChangeDetectionStrategy, Component, computed, output } from '@angular/core';

import { shortChunkId } from '../chunk-id';
import { injectHubQuestionsQuery } from './questions.query';

/**
 * The open-questions panel (D-052, MVP criterion 7) — every agent ask across the
 * fleet, in the right rail. A parked chunk's question is the one thing on this
 * board that blocks a worker on a human, so it is surfaced fleet-wide rather than
 * only inside the chunk nobody has selected yet; clicking an ask opens its chunk,
 * where the answer is given.
 *
 * A container: it owns the fleet-wide questions query through the generated hub
 * client (bzh:generated-client); the live-update service re-reads it on
 * `question-asked` / `question-answered`.
 *
 * Its test handles are `rail-`prefixed because the chunk detail dock renders the
 * *same* chunk's ask at the same time, under `open-question` / `question-text`. Two
 * components sharing a handle makes a browser test's locator ambiguous — it matches
 * both and fails strict-mode — so the rail names its own.
 */
@Component({
  selector: 'fleet-questions-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="panel" aria-label="Open questions" data-testid="questions-panel">
      <div class="panel-head">
        <span class="lbl">Open questions · agent asks</span>
        <span class="lbl" data-testid="questions-count">{{ questions().length || '' }}</span>
      </div>
      <div class="panel-body">
        @if (questions().length === 0) {
          <p class="none" data-testid="questions-empty">NO OPEN QUESTIONS</p>
        } @else {
          @for (q of questions(); track q.question_id) {
            <button
              type="button"
              class="qq"
              data-testid="rail-question"
              [attr.data-chunk]="q.chunk_id"
              [attr.aria-label]="'Open chunk ' + shortId(q.chunk_id) + ' to answer its question'"
              (click)="selectChunk.emit(q.chunk_id)"
            >
              <span class="h">
                <span class="cid" data-testid="rail-question-chunk">{{ shortId(q.chunk_id) }}</span>
                <span class="rid">{{ q.runner_id }}</span>
              </span>
              <span class="qt" data-testid="rail-question-text">{{ q.question }}</span>
              @if (q.options && q.options.length > 0) {
                <span class="opts" data-testid="rail-question-options">{{ q.options.join(' · ') }}</span>
              }
            </button>
          }
        }
      </div>
    </section>
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      min-height: 0;
      flex: 1;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    .panel {
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border: 1px solid var(--bezel);
      display: flex;
      flex-direction: column;
      min-height: 0;
      flex: 1;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.25);
      flex: none;
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 rgba(0, 0, 0, 0.9);
    }
    .panel-body {
      overflow-y: auto;
      overflow-x: hidden;
      flex: 1;
      min-height: 0;
    }
    .qq {
      display: flex;
      flex-direction: column;
      gap: 3px;
      width: 100%;
      text-align: left;
      padding: 6px 8px;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: transparent;
      font: inherit;
      color: inherit;
      cursor: pointer;
    }
    .qq:hover {
      background: color-mix(in srgb, var(--amber) 6%, transparent);
    }
    .qq:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: -1px;
    }
    .h {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 6px;
    }
    .cid {
      color: var(--amber-hi);
      font-size: var(--fs-sm);
    }
    .rid {
      color: var(--label-dim);
      font-size: var(--fs-label);
      white-space: nowrap;
    }
    .qt {
      color: var(--text);
      font-size: var(--fs-sm);
      line-height: 1.5;
      overflow-wrap: anywhere;
    }
    .opts {
      color: var(--cyan);
      font-size: var(--fs-xs);
      overflow-wrap: anywhere;
    }
    .none {
      color: var(--label-dim);
      padding: 10px 8px;
      margin: 0;
      font-size: var(--fs-sm);
      letter-spacing: 0.08em;
    }
  `,
})
export class QuestionsPanel {
  private readonly query = injectHubQuestionsQuery();

  /** Emitted with a chunk id when an ask is activated — opens it in the detail panel. */
  readonly selectChunk = output<string>();

  /** Every open ask across the fleet; empty until the first read resolves. */
  protected readonly questions = computed(() => this.query.data() ?? []);

  protected shortId(chunkId: string): string {
    return shortChunkId(chunkId);
  }
}
