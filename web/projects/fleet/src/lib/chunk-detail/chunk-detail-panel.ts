import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { ArtifactView, ChunkDetail, DecisionView, EscalationView, QuestionView, TransitionView } from '../api/hub';

/** Emitted when the operator answers a chunk's open question from the drawer. */
export interface AnswerQuestionEvent {
  readonly questionId: string;
  readonly answer: string;
  readonly chunkId: string;
}

/** Emitted when the operator resolves a chunk's open gate decision from the drawer. */
export interface ResolveDecisionEvent {
  readonly decisionId: string;
  readonly choice: string;
  readonly chunkId: string;
}

/**
 * The chunk detail drawer — a chunk's node history and its artifact store (D-036,
 * MVP criterion 9/11). Slides over the board when a card is selected and renders:
 *
 * - the **awaiting-human** state, when the chunk is parked (`waiting_on_human`): its
 *   open **question** with an inline **Answer** action (MVP criterion 7, D-052) and/or
 *   its open gate **decision** rendered as **choice buttons** the operator resolves
 *   from the board (D-042, MVP criterion 12);
 * - the **escalation** state, when the chunk `needs_human`: the copyable **takeover
 *   command** the operator runs to enter the parked worker's session (D-009);
 * - the **transition history**, oldest-first: every edge the chunk took, so the
 *   review-fail loop back to build reads as a visible `review → build (fail)` step;
 * - the **artifact store**: each entry keyed `{node}.{artifact-name}.{epoch}`, with an
 *   **asset's** findings text shown inline (the review notes a fail carried back) and a
 *   **git_commit's** pinned `repo @ commit` reference (the branch pointers merged).
 *
 * Presentational only: it holds the detail input and emits `dismiss`, `answerQuestion`,
 * and `resolveDecision`; the data client (the mutations those events drive) lives in the
 * container. All color comes from the design-token layer, never hard-coded.
 */
@Component({
  selector: 'fleet-chunk-detail-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="drawer" data-testid="chunk-detail" role="dialog" aria-label="Chunk detail">
      <header class="d-head">
        <div class="d-title">
          <span class="lbl">Chunk</span>
          <span class="id" data-testid="detail-id">{{ detail().chunk_id }}</span>
        </div>
        <div class="d-meta">
          <span class="st" data-testid="detail-status">{{ detail().status }}</span>
          <span class="nd" data-testid="detail-node">{{ detail().current_node_id ?? '—' }}</span>
        </div>
        <button type="button" class="close" data-testid="detail-close" aria-label="Close" (click)="dismiss.emit()">
          ✕
        </button>
      </header>

      @if (openQuestions().length > 0 || openDecision()) {
        <section class="d-section awaiting" aria-label="Awaiting human" data-testid="awaiting-human">
          <div class="s-head"><span class="lbl">Awaiting human</span></div>
          @for (q of openQuestions(); track q.question_id) {
            <div class="ask" data-testid="open-question">
              <p class="ask-q" data-testid="question-text">{{ q.question }}</p>
              @if (q.options && q.options.length > 0) {
                <div class="chips">
                  @for (opt of q.options; track opt) {
                    <button
                      type="button"
                      class="chip act"
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
                <button
                  type="button"
                  class="act primary"
                  data-testid="answer-submit"
                  (click)="submitAnswer(q.question_id, answerInput.value); answerInput.value = ''"
                >
                  Answer
                </button>
              </div>
            </div>
          }
          @if (openDecision(); as d) {
            <div class="gate" data-testid="open-decision">
              <div class="gate-head">
                <span class="lbl">Gate</span>
                <span class="gate-node" data-testid="decision-node">{{ d.node_name }}</span>
              </div>
              <div class="chips">
                @for (c of d.choices ?? []; track c.name) {
                  <button
                    type="button"
                    class="chip act primary"
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
        </section>
      }

      @if (escalation(); as esc) {
        <section class="d-section escalation" aria-label="Escalation" data-testid="escalation">
          <div class="s-head"><span class="lbl">Needs human · takeover</span></div>
          <p class="esc-hint">The worker escalated (epoch {{ esc.epoch }}). Run the takeover command to enter its session:</p>
          <div class="takeover">
            <code class="cmd" data-testid="takeover-command">{{ esc.takeover_command }}</code>
            <button type="button" class="act" data-testid="copy-takeover" (click)="copyTakeover(esc.takeover_command)">
              {{ copied() ? 'Copied' : 'Copy' }}
            </button>
          </div>
        </section>
      }

      <section class="d-section" aria-label="Node history">
        <div class="s-head"><span class="lbl">Node history</span></div>
        @if (history().length === 0) {
          <p class="none" data-testid="history-empty">No transitions yet — waiting on the first node-step.</p>
        } @else {
          <ol class="timeline" data-testid="history">
            @for (step of history(); track $index) {
              <li class="step" data-testid="history-step" [attr.data-choice]="step.choice_name">
                <span class="from">{{ step.from_node_id ?? '·' }}</span>
                <span class="arrow">→</span>
                <span class="to">{{ step.to_node_id }}</span>
                @if (step.choice_name) {
                  <span class="choice" data-testid="history-choice">{{ step.choice_name }}</span>
                }
                <span class="epoch">e{{ step.epoch }}</span>
              </li>
            }
          </ol>
        }
      </section>

      <section class="d-section" aria-label="Artifacts">
        <div class="s-head"><span class="lbl">Artifacts</span></div>
        @if (artifacts().length === 0) {
          <p class="none" data-testid="artifacts-empty">No artifacts yet.</p>
        } @else {
          <ul class="artifacts" data-testid="artifacts">
            @for (art of artifacts(); track art.key) {
              <li class="artifact" data-testid="artifact" [attr.data-kind]="art.kind">
                <div class="a-head">
                  <span class="a-key" data-testid="artifact-key">{{ art.key }}</span>
                  <span class="a-kind">{{ art.kind }}</span>
                </div>
                @if (art.kind === 'asset') {
                  <pre class="a-content" data-testid="artifact-content">{{ art.content }}</pre>
                } @else {
                  <div class="a-ref" data-testid="artifact-ref">{{ art.repo }} @ {{ art.commit_hash }}</div>
                }
              </li>
            }
          </ul>
        }
      </section>
    </aside>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      font-family: var(--mono);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }
    .lbl {
      font-size: 9px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .drawer {
      display: flex;
      flex-direction: column;
      height: 100%;
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border-left: 1px solid var(--bezel);
      color: var(--text);
      overflow-y: auto;
    }
    .d-head {
      display: grid;
      grid-template-columns: 1fr auto auto;
      align-items: center;
      gap: 8px;
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.25);
    }
    .d-title {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .d-title .id {
      color: var(--cyan);
      font-size: 11px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .d-meta {
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 2px;
    }
    .d-meta .st {
      color: var(--amber-hi);
      font-size: 9px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
    }
    .d-meta .nd {
      color: var(--label-dim);
      font-size: 9px;
    }
    .close {
      background: transparent;
      border: 1px solid var(--line);
      color: var(--label-dim);
      cursor: pointer;
      font-family: inherit;
      padding: 2px 6px;
    }
    .close:hover {
      color: var(--text);
    }
    .d-section {
      border-bottom: 1px solid var(--line);
      padding: 6px 8px;
    }
    .s-head {
      margin-bottom: 6px;
    }
    .none {
      color: var(--label-dim);
      font-size: 10px;
    }
    .awaiting {
      border-left: 2px solid var(--amber-hi);
    }
    .ask,
    .gate {
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.2);
      padding: 4px 6px;
    }
    .gate {
      margin-top: 4px;
    }
    .ask-q {
      margin: 0 0 4px;
      color: var(--text);
      font-size: 11px;
    }
    .gate-head {
      display: flex;
      align-items: baseline;
      gap: 6px;
      margin-bottom: 4px;
    }
    .gate-node {
      color: var(--cyan);
      font-size: 11px;
    }
    .chips {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
    .chip {
      border: 1px solid var(--line);
      color: var(--amber-hi);
      font-size: 9px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      padding: 1px 5px;
    }
    .act {
      font-family: inherit;
      background: rgba(0, 0, 0, 0.3);
      border: 1px solid var(--line);
      color: var(--text);
      cursor: pointer;
      padding: 3px 8px;
      font-size: 10px;
    }
    .act:hover {
      border-color: var(--cyan);
    }
    .act:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: 1px;
    }
    button.chip.act {
      cursor: pointer;
    }
    .act.primary {
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
      font-size: 11px;
      background: rgba(0, 0, 0, 0.35);
      border: 1px solid var(--line);
      color: var(--text);
      padding: 3px 6px;
    }
    .answer-input:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: 0;
    }
    .escalation {
      border-left: 2px solid var(--danger, #e06c75);
    }
    .esc-hint {
      margin: 0 0 6px;
      color: var(--label-dim);
      font-size: 10px;
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
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid var(--line);
      color: var(--amber-hi);
      padding: 4px 6px;
      font-size: 11px;
    }
    .timeline {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 3px;
    }
    .step {
      display: flex;
      align-items: baseline;
      gap: 6px;
      padding: 3px 5px;
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.2);
    }
    .step .from,
    .step .to {
      color: var(--cyan);
    }
    .step .arrow {
      color: var(--label-dim);
    }
    .step .choice {
      color: var(--amber-hi);
      font-size: 9px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    .step[data-choice='fail'] .choice {
      color: var(--danger, #e06c75);
    }
    .step .epoch {
      margin-left: auto;
      color: var(--label-dim);
      font-size: 9px;
    }
    .artifacts {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .artifact {
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.2);
      padding: 4px 5px;
    }
    .a-head {
      display: flex;
      justify-content: space-between;
      gap: 6px;
    }
    .a-key {
      color: var(--cyan);
      font-size: 10px;
    }
    .a-kind {
      color: var(--label-dim);
      font-size: 9px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .a-content {
      margin: 4px 0 0;
      padding: 4px;
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(0, 0, 0, 0.3);
      color: var(--text);
      font-size: 11px;
    }
    .a-ref {
      margin-top: 4px;
      color: var(--label-dim);
      font-size: 10px;
    }
  `,
})
export class ChunkDetailPanel {
  /** The chunk aggregate to render (status, current node, history, artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** Emitted when the operator dismisses the drawer. */
  readonly dismiss = output<void>();

  /** Emitted when the operator answers an open question (MVP criterion 7). */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision (D-042). */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Transient "Copied" state for the takeover-command copy button. */
  protected readonly copied = signal(false);

  protected readonly history = computed<readonly TransitionView[]>(() => this.detail().history ?? []);
  protected readonly artifacts = computed<readonly ArtifactView[]>(() => this.detail().artifacts ?? []);

  /** The chunk's open (unanswered) questions — the ask a parked chunk waits on (D-004). */
  protected readonly openQuestions = computed<readonly QuestionView[]>(() =>
    (this.detail().questions ?? []).filter((q) => !q.answered),
  );

  /** The chunk's live gate decision while it still awaits the resolving transition (D-045). */
  protected readonly openDecision = computed<DecisionView | null>(() => {
    const decision = this.detail().decision;
    return decision && !decision.transitioned ? decision : null;
  });

  /** The chunk's open escalation, if it currently needs a human takeover (D-009). */
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
