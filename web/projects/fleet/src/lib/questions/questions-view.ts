import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { QuestionView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitPanel } from '../kit/kit-panel';

/**
 * The open-questions rail's presentational half (issue #80) — the ask list
 * and the click-to-open row. Renders exactly the questions it is handed;
 * injects no query.
 *
 * Its test handles are `rail-`prefixed because the chunk detail dock renders the
 * *same* chunk's ask at the same time, under `open-question` / `question-text`. Two
 * components sharing a handle makes a browser test's locator ambiguous — it matches
 * both and fails strict-mode — so the rail names its own.
 */
@Component({
  selector: 'fleet-questions-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel],
  template: `
    <fleet-kit-panel
      class="fill"
      aria-label="Open questions"
      data-testid="questions-panel"
      label="Open questions · agent asks"
      [count]="questions().length || null"
      countTestid="questions-count"
    >
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
    </fleet-kit-panel>
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
    fleet-kit-panel.fill {
      flex: 1;
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
export class QuestionsPanelView {
  /** Every open ask across the fleet. */
  readonly questions = input.required<readonly QuestionView[]>();

  /** Emitted with a chunk id when an ask is activated — opens it in the detail panel. */
  readonly selectChunk = output<string>();

  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }
}
