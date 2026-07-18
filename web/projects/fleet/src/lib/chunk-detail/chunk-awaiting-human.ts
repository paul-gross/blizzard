import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { ChunkDetail, DecisionView, EscalationView, QuestionView } from '../api/hub';
import { KitButton } from '../kit/kit-button';

/** Emitted when the operator answers a chunk's open question from the dock. */
export interface AnswerQuestionEvent {
  readonly questionId: string;
  readonly answer: string;
  readonly chunkId: string;
}

/** Emitted when the operator resolves a chunk's open gate decision from the dock. */
export interface ResolveDecisionEvent {
  readonly decisionId: string;
  readonly choice: string;
  readonly chunkId: string;
}

/**
 * The chunk's awaiting-human gate (issue #79) — whatever the chunk waits on
 * a human for: an open **question** with an inline **Answer** action (MVP
 * criterion 7), an open gate **decision** as **choice buttons** (MVP
 * criterion 12), or an **escalation's** copyable **takeover command**.
 * Presentational only: it holds the detail input and emits `answerQuestion`
 * / `resolveDecision`; the mutations those events drive live in the
 * container.
 */
@Component({
  selector: 'fleet-chunk-detail-awaiting-human',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  template: `
    @if (openQuestions().length > 0 || openDecision()) {
      <div class="awaiting" data-testid="awaiting-human">
        <div class="s-head"><span class="tag">Awaiting human</span></div>
        @for (q of openQuestions(); track q.question_id) {
          <div class="ask" data-testid="open-question">
            <p class="ask-q" data-testid="question-text">{{ q.question }}</p>
            @if (q.options && q.options.length > 0) {
              <div class="chips">
                @for (opt of q.options; track opt) {
                  <button
                    type="button"
                    class="chip"
                    data-testid="question-option"
                    (click)="submitAnswer(q.question_id, opt)"
                  >
                    {{ opt }}
                  </button>
                }
              </div>
            }
            <div class="answer-row">
              <input
                #answerInput
                class="answer-input"
                type="text"
                data-testid="answer-input"
                placeholder="Type an answer…"
                [attr.aria-label]="'Answer question ' + q.question_id"
              />
              <fleet-kit-button
                variant="primary"
                testid="answer-submit"
                (click)="submitAnswer(q.question_id, answerInput.value); answerInput.value = ''"
              >
                Answer
              </fleet-kit-button>
            </div>
          </div>
        }
        @if (openDecision(); as d) {
          <div class="gate" data-testid="open-decision">
            <div class="gate-head">
              <span class="tag">Gate</span>
              <span class="gate-node" data-testid="decision-node">{{ d.node_name }}</span>
            </div>
            <div class="chips">
              @for (c of d.choices ?? []; track c.name) {
                <button
                  type="button"
                  class="chip primary"
                  data-testid="decision-choice"
                  [title]="c.description"
                  (click)="resolve(d.decision_id, c.name)"
                >
                  {{ c.name }}
                </button>
              }
            </div>
          </div>
        }
      </div>
    }

    @if (escalation(); as esc) {
      <div class="escalation" data-testid="escalation">
        <div class="s-head"><span class="tag">Needs human · takeover</span></div>
        <p class="esc-hint">The worker escalated (epoch {{ esc.epoch }}). Run the takeover command to enter its session:</p>
        <div class="takeover">
          <code class="cmd" data-testid="takeover-command">{{ esc.takeover_command }}</code>
          <fleet-kit-button testid="copy-takeover" (click)="copyTakeover(esc.takeover_command)">
            {{ copied() ? 'Copied' : 'Copy' }}
          </fleet-kit-button>
        </div>
      </div>
    }
  `,
  styles: `
    :host {
      display: contents;
    }
    .tag {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .awaiting,
    .escalation {
      margin-bottom: 8px;
    }
    .s-head {
      margin-bottom: 6px;
    }
    /* The same amber accent bar the board cards carry, with breathing room so the
       heading never touches it. */
    .awaiting {
      border-left: 2px solid var(--amber);
      padding: 4px 0 4px 8px;
    }
    .ask,
    .gate {
      border: 1px solid var(--line);
      background: var(--overlay-20);
      padding: 4px 6px;
    }
    .gate {
      margin-top: 4px;
    }
    .ask-q {
      margin: 0 0 4px;
      color: var(--text);
      font-size: var(--fs-sm);
    }
    .gate-head {
      display: flex;
      align-items: baseline;
      gap: 6px;
      margin-bottom: 4px;
    }
    .gate-node {
      color: var(--cyan);
      font-size: var(--fs-sm);
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    /* The ask/gate chips carry a distinct engraved look (uppercase, letter-spaced)
       the shared kit's plain chip/button do not — a local, deliberate variant. */
    .chip {
      font-family: inherit;
      background: var(--overlay-30);
      border: 1px solid var(--line);
      color: var(--text);
      cursor: pointer;
      padding: 3px 8px;
      font-size: var(--fs-xs);
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .chip:hover {
      border-color: var(--cyan);
    }
    .chip:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: 1px;
    }
    .chip.primary {
      color: var(--cyan);
    }
    .answer-row {
      display: flex;
      gap: 4px;
      margin-top: 6px;
    }
    .answer-input {
      flex: 1;
      min-width: 0;
      font-family: inherit;
      font-size: var(--fs-sm);
      background: var(--overlay-35);
      border: 1px solid var(--line);
      color: var(--text);
      padding: 3px 6px;
    }
    .answer-input:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: 0;
    }
    .escalation {
      border: 1px solid var(--red-dim);
      background: color-mix(in srgb, var(--red) 6%, transparent);
      padding: 6px;
    }
    .esc-hint {
      margin: 0 0 6px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .takeover {
      display: flex;
      gap: 4px;
      align-items: stretch;
    }
    .takeover .cmd {
      flex: 1;
      min-width: 0;
      overflow-x: auto;
      white-space: pre;
      background: var(--overlay-40);
      border: 1px solid var(--line);
      color: var(--amber-hi);
      padding: 4px 6px;
      font-size: var(--fs-sm);
    }
  `,
})
export class ChunkAwaitingHuman {
  /** The chunk aggregate to render (open questions, gate decision, escalation). */
  readonly detail = input.required<ChunkDetail>();

  /** Emitted when the operator answers an open question (MVP criterion 7). */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Transient "Copied" state for the takeover-command copy button. */
  protected readonly copied = signal(false);

  /** The chunk's open (unanswered) questions — the ask a parked chunk waits on. */
  protected readonly openQuestions = computed<readonly QuestionView[]>(() =>
    (this.detail().questions ?? []).filter((q) => !q.answered),
  );

  /** The chunk's live gate decision while it still awaits the resolving transition. */
  protected readonly openDecision = computed<DecisionView | null>(() => {
    const decision = this.detail().decision;
    return decision && !decision.transitioned ? decision : null;
  });

  /** The chunk's open escalation, if it currently needs a human takeover. */
  protected readonly escalation = computed<EscalationView | null>(() => this.detail().escalation ?? null);

  /** Emit an answer for a question — no-op on an empty answer. */
  protected submitAnswer(questionId: string, answer: string): void {
    const trimmed = answer.trim();
    if (!trimmed) return;
    this.answerQuestion.emit({ questionId, answer: trimmed, chunkId: this.detail().chunk_id });
  }

  /** Emit a resolution for the open gate decision. */
  protected resolve(decisionId: string, choice: string): void {
    this.resolveDecision.emit({ decisionId, choice, chunkId: this.detail().chunk_id });
  }

  /** Copy the takeover command to the clipboard, flashing "Copied" when it lands. */
  protected copyTakeover(command: string): void {
    const clipboard = globalThis.navigator?.clipboard;
    if (!clipboard) return;
    void clipboard.writeText(command).then(() => {
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 1500);
    });
  }
}
