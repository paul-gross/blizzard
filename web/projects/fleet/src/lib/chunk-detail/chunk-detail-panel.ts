import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type {
  ArtifactView,
  ChunkDetail,
  DecisionView,
  EscalationView,
  PmItemEntry,
  QuestionView,
  TransitionView,
} from '../api/hub';

/** The chunk's related PM items and the state of the pass-through fetch, for the Issue tab.
 *
 * `loading` while the forge read is in flight, `error` when the whole read failed (an
 * unreachable hub or no work-source configured — the tab shows a visible notice, AC5), and
 * `success` with `items` (possibly empty for a chunk with no pointers — the empty state, AC4;
 * a per-item `error` carries a single pointer's forge failure the tab notices in place). */
export interface PmItemsState {
  readonly status: 'loading' | 'error' | 'success';
  readonly items: readonly PmItemEntry[];
}

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
 * The chunk detail dock — a chunk's node history and its artifact store (D-036,
 * MVP criterion 9/11). Fills the bottom dock beneath the board when a card is
 * selected — without reflowing the board columns — and renders:
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
    <aside class="dock" data-testid="chunk-detail" role="region" aria-label="Chunk detail">
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

      <nav class="tabs" role="tablist" aria-label="Chunk detail sections">
        @for (t of tabs; track t.key) {
          <button
            type="button"
            class="tab"
            role="tab"
            [class.active]="activeTab() === t.key"
            [attr.aria-selected]="activeTab() === t.key"
            [attr.data-testid]="'tab-' + t.key"
            (click)="activeTab.set(t.key)"
          >
            {{ t.label }}@if (t.key === 'issue') {<span class="tab-count" data-testid="issue-count">{{ pointerCount() }}</span>}
          </button>
        }
      </nav>

      @if (activeTab() === 'issue') {
        <section class="d-section" aria-label="Issue" data-testid="issue-pane">
          @switch (pmItems().status) {
            @case ('loading') {
              <p class="none" data-testid="issue-loading">Loading issue…</p>
            }
            @case ('error') {
              <p class="notice" data-testid="issue-error">
                Could not reach the forge — issue content is unavailable.
              </p>
            }
            @default {
              @if (pmItems().items.length === 0) {
                <p class="none" data-testid="issue-empty">This chunk has no linked issue.</p>
              } @else {
                @for (item of pmItems().items; track item.url) {
                  <article class="issue" data-testid="issue-item">
                    <div class="i-head">
                      <a
                        class="i-label"
                        data-testid="issue-label"
                        [href]="item.url"
                        target="_blank"
                        rel="noreferrer"
                      >{{ item.label ?? item.url }}</a>
                    </div>
                    @if (item.error) {
                      <p class="notice" data-testid="issue-item-error">
                        Could not load this issue — {{ item.error }}
                      </p>
                    } @else {
                      <pre class="i-body" data-testid="issue-body">{{ item.body }}</pre>
                      <div class="i-messages">
                        <div class="s-head"><span class="lbl">Messages · {{ (item.comments ?? []).length }}</span></div>
                        @if ((item.comments ?? []).length === 0) {
                          <p class="none" data-testid="issue-no-messages">No messages.</p>
                        } @else {
                          <ul class="messages" data-testid="issue-messages">
                            @for (c of item.comments ?? []; track $index) {
                              <li class="message" data-testid="issue-message"><pre class="m-body">{{ c }}</pre></li>
                            }
                          </ul>
                        }
                      </div>
                    }
                  </article>
                }
              }
            }
          }
        </section>
      } @else {
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
                <span class="from" [attr.title]="step.from_node_id || null">{{
                  step.from_node_name ?? step.from_node_id ?? '·'
                }}</span>
                <span class="arrow">→</span>
                <span class="to" [attr.title]="step.to_node_id || null">{{
                  step.to_node_name ?? step.to_node_id
                }}</span>
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
                  <div class="a-ref" data-testid="artifact-ref">
                    <span class="a-repo">{{ art.repo }}</span>
                    @if (art.branch_name) {
                      <span class="a-sep">·</span>
                      @if (art.branch_url) {
                        <a
                          class="a-branch"
                          data-testid="artifact-branch"
                          [href]="art.branch_url"
                          target="_blank"
                          rel="noreferrer"
                          [attr.title]="art.branch_url"
                          >{{ art.branch_name }}</a
                        >
                      } @else {
                        <span class="a-branch" data-testid="artifact-branch">{{ art.branch_name }}</span>
                      }
                    }
                    <span class="a-commit">&#64; {{ art.commit_hash }}</span>
                  </div>
                }
              </li>
            }
          </ul>
        }
      </section>
      }
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
    .dock {
      display: flex;
      flex-direction: column;
      height: 100%;
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border-top: 1px solid var(--bezel);
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
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 4px;
    }
    .a-branch {
      color: var(--amber-hi);
    }
    a.a-branch {
      text-decoration: none;
    }
    a.a-branch:hover,
    a.a-branch:focus-visible {
      text-decoration: underline;
      outline: none;
    }
    .a-sep {
      color: var(--label-dim);
    }
    .tabs {
      display: flex;
      gap: 0;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.2);
    }
    .tab {
      display: flex;
      align-items: center;
      gap: 5px;
      font-family: inherit;
      background: transparent;
      border: none;
      border-bottom: 2px solid transparent;
      color: var(--label-dim);
      cursor: pointer;
      font-size: 9px;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      padding: 6px 10px;
    }
    .tab:hover {
      color: var(--text);
    }
    .tab.active {
      color: var(--cyan);
      border-bottom-color: var(--cyan);
    }
    .tab:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: -1px;
    }
    .tab-count {
      color: var(--label-dim);
      font-size: 9px;
      letter-spacing: 0;
    }
    .tab.active .tab-count {
      color: var(--cyan);
    }
    .notice {
      margin: 0;
      padding: 4px 6px;
      border: 1px solid var(--danger, #e06c75);
      border-left-width: 2px;
      background: rgba(0, 0, 0, 0.2);
      color: var(--danger, #e06c75);
      font-size: 10px;
    }
    .issue {
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.2);
      padding: 5px 6px;
    }
    .issue + .issue {
      margin-top: 6px;
    }
    .i-head {
      margin-bottom: 4px;
    }
    .i-label {
      color: var(--cyan);
      font-size: 11px;
      text-decoration: none;
      overflow-wrap: anywhere;
    }
    .i-label:hover {
      text-decoration: underline;
    }
    .i-body {
      margin: 0;
      padding: 4px;
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(0, 0, 0, 0.3);
      color: var(--text);
      font-size: 11px;
    }
    .i-messages {
      margin-top: 6px;
    }
    .messages {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .m-body {
      margin: 0;
      padding: 4px;
      white-space: pre-wrap;
      word-break: break-word;
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.25);
      color: var(--text);
      font-size: 11px;
    }
  `,
})
export class ChunkDetailPanel {
  /** The chunk aggregate to render (status, current node, history, artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's related PM items + fetch state, rendered by the Issue tab (issue #24).
   * Defaults to `loading` so the panel constructs without the container wiring it. */
  readonly pmItems = input<PmItemsState>({ status: 'loading', items: [] });

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** Emitted when the operator answers an open question (MVP criterion 7). */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision (D-042). */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** The dock's tabs — the existing chunk detail, and the related issue content (issue #24). */
  protected readonly tabs = [
    { key: 'detail', label: 'Detail' },
    { key: 'issue', label: 'Issue' },
  ] as const;

  /** The open tab; the issue content is its own tab, never a replacement of chunk detail. */
  protected readonly activeTab = signal<'detail' | 'issue'>('detail');

  /** The chunk's PM pointer count — the Issue tab's badge, legible before the read lands. */
  protected readonly pointerCount = computed<number>(() => this.detail().pm_pointers?.length ?? 0);

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
