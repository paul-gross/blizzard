import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type {
  ArtifactView,
  ChunkDetail,
  DecisionView,
  EscalationView,
  PmItemEntry,
  QuestionView,
  RouteView,
  TransitionView,
} from '../api/hub';
import { shortChunkId } from '../chunk-id';

/** The chunk's related PM items and the state of the pass-through fetch, for the work-item column.
 *
 * `loading` while the forge read is in flight, `error` when the whole read failed (an
 * unreachable hub or no work-source configured — the column shows a visible notice, AC5), and
 * `success` with `items` (possibly empty for a chunk with no pointers — the empty state, AC4;
 * a per-item `error` carries a single pointer's forge failure the column notices in place). */
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
 * The chunk detail dock (D-036, MVP criterion 9/11) — everything known about the
 * selected chunk, filling the centre column under the board without reflowing it.
 *
 * A header carries the chunk's identity in the board's own vocabulary (the short
 * name, its work item, its state, and the node it sits at) alongside the **route +
 * Detach** control — shown whenever the chunk carries a live route: the one place an
 * operator verb with a real failure mode (409, no live route to release) lives on the
 * board (issue #42). Detach is deliberately **not** requeue — it supersedes no
 * escalation and bumps no epoch, so a `needs_human` chunk detached this way still
 * derives `needs_human` afterward (`src/blizzard/hub/domain/detach.py`); this dock
 * never claims otherwise.
 *
 * Under the header sit three columns of roughly equal weight — **what it is**,
 * **where it has been**, **what it produced**:
 *
 * - **the work item** — the chunk's PM pointers, each a link out to the forge (the
 *   board cards deliberately carry none), the issue title and body pass-through
 *   (issue #24), and the chunk's own facts: status, node, agent, attempts;
 * - **node history**, oldest-first: every edge the chunk took with the judgement
 *   that chose it, so the review-fail loop back to build reads as a visible
 *   `review → build (fail)` step;
 * - **artifacts and asks** — the **artifact store**, each entry keyed
 *   `{node}.{artifact-name}.{epoch}`, with an **asset's** findings text inline and a
 *   **git_commit's** pinned `repo @ commit` reference; above it, whatever the chunk
 *   waits on a human for: an open **question** with an inline **Answer** action (MVP
 *   criterion 7, D-052), an open gate **decision** as **choice buttons** (D-042, MVP
 *   criterion 12), or an **escalation's** copyable **takeover command** (D-009).
 *
 * Presentational only: it holds the detail input and emits `dismiss`, `answerQuestion`,
 * `resolveDecision`, and `detach` (guarded by a `confirm()`, the one browser affordance
 * this panel already reaches for — see `copyTakeover`); the data client (the mutation
 * `detach` drives, and the error it surfaces back down as `detachError`) lives in the
 * container. All color comes from the design-token layer, never hard-coded, and every
 * text size from that layer's type scale.
 */
@Component({
  selector: 'fleet-chunk-detail-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="dock" data-testid="chunk-detail" role="region" aria-label="Chunk detail">
      <!-- The chunk's identity, in the board's own vocabulary: the short name in gold,
           the work item it serves in cyan, and its state — with the node it currently
           sits at pushed to the far right, the same shape the board cards use. -->
      <header class="d-head">
        <div class="d-title">
          <span class="id" data-testid="detail-id" [title]="detail().chunk_id">{{ shortId() }}</span>
          <span class="d-sub">
            @if (pointerLabel()) {
              <span class="iss" data-testid="detail-pointer">{{ pointerLabel() }}</span>
            }
            <span class="st" data-testid="detail-status">{{ detail().status }}</span>
          </span>
        </div>
        <div class="d-meta">
          <!-- The route rides in the header beside Detach, not down in the facts, because
               it is the button's object: it names what is about to be released (issue #42).
               The work-item column states the same runner as a plain fact. -->
          @if (route(); as r) {
            <div class="d-route" data-testid="route-info">
              <span class="lbl">Route</span>
              <span class="rn" data-testid="route-runner" [attr.title]="r.runner_id">{{ r.runner_id }}</span>
              <button
                type="button"
                class="act danger"
                data-testid="detach-chunk"
                [attr.aria-label]="'Detach chunk ' + detail().chunk_id + ' from its runner'"
                (click)="onDetach()"
              >
                Detach
              </button>
            </div>
          }
          <span class="nd" data-testid="detail-node" [attr.title]="detail().current_node_id">{{
            detail().current_node_name ?? detail().current_node_id ?? '—'
          }}</span>
          <button type="button" class="close" data-testid="detail-close" aria-label="Close" (click)="dismiss.emit()">
            ✕
          </button>
        </div>
      </header>

      @if (detachError(); as err) {
        <p class="notice detach-notice" data-testid="detach-error" role="alert">{{ err }}</p>
      }

      <div class="d-body">
        <section class="d-sec" aria-label="Work item" data-testid="issue-pane">
          <div class="s-head"><span class="lbl">Work item · {{ pointerCount() }}</span></div>
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
                @for (item of pmItems().items; track item.source + ':' + item.ref) {
                  <article class="issue" data-testid="issue-item">
                    <!-- The link out to the PM lives here and only here: the board cards
                         are click targets for opening a chunk, so an anchor on them
                         competes with that. This is where the operator leaves for the forge. -->
                    <div class="i-head">
                      <a
                        class="i-label"
                        data-testid="issue-label"
                        [href]="item.web_url"
                        target="_blank"
                        rel="noreferrer"
                        [attr.title]="item.web_url"
                      >{{ item.label ?? (item.source + '#' + item.ref) }}</a>
                    </div>
                    @if (item.error) {
                      <p class="notice" data-testid="issue-item-error">
                        Could not load this issue — {{ item.error }}
                      </p>
                    } @else {
                      @if (item.title) {
                        <p class="i-title" data-testid="issue-title">{{ item.title }}</p>
                      }
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

          <dl class="kv" data-testid="chunk-facts">
            <dt>Status</dt>
            <dd data-testid="fact-status">{{ detail().status }}</dd>
            <dt>Node</dt>
            <dd data-testid="fact-node">{{ detail().current_node_name ?? detail().current_node_id ?? '—' }}</dd>
            <dt>Agent</dt>
            <dd data-testid="fact-agent">{{ agent() }}</dd>
            <dt>Attempts</dt>
            <dd data-testid="fact-attempts">{{ attempts() }}</dd>
          </dl>
        </section>
        <section class="d-sec" aria-label="Node history">
          <div class="s-head"><span class="lbl">Node history</span></div>
          @if (history().length === 0) {
            <p class="none" data-testid="history-empty">No transitions yet — waiting on the first node-step.</p>
          } @else {
            <ol class="timeline" data-testid="history">
              @for (step of history(); track $index) {
                <li class="step" data-testid="history-step" [attr.data-choice]="step.choice_name">
                  <span class="att">{{ step.epoch }}</span>
                  <span class="nd">
                    <span class="from" [attr.title]="step.from_node_id || null">{{
                      step.from_node_name ?? step.from_node_id ?? '·'
                    }}</span>
                    <span class="arrow">→</span>
                    <span class="to" [attr.title]="step.to_node_id || null">{{
                      step.to_node_name ?? step.to_node_id
                    }}</span>
                  </span>
                  <!-- The edge the graph chose out of that node — a review's PASS/FAIL is
                       the judgement that sent the chunk on or looped it back to build. -->
                  @if (step.choice_name) {
                    <span class="jg" data-testid="history-choice">{{ step.choice_name }}</span>
                  }
                </li>
              }
            </ol>
          }
        </section>

        <section class="d-sec" aria-label="Artifacts and asks">
      @if (openQuestions().length > 0 || openDecision()) {
        <div class="awaiting" data-testid="awaiting-human">
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
        </div>
      }

      @if (escalation(); as esc) {
        <div class="escalation" data-testid="escalation">
          <div class="s-head"><span class="lbl">Needs human · takeover</span></div>
          <p class="esc-hint">The worker escalated (epoch {{ esc.epoch }}). Run the takeover command to enter its session:</p>
          <div class="takeover">
            <code class="cmd" data-testid="takeover-command">{{ esc.takeover_command }}</code>
            <button type="button" class="act" data-testid="copy-takeover" (click)="copyTakeover(esc.takeover_command)">
              {{ copied() ? 'Copied' : 'Copy' }}
            </button>
          </div>
        </div>
      }

      <div class="arts">
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
      </div>
        </section>
      </div>
    </aside>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      min-height: 0;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    /* The dock itself never scrolls — each of its three columns owns its own
       scroll, so a long issue body cannot push the history and artifacts out of
       view, and the header stays pinned. */
    .dock {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border: 1px solid var(--bezel);
      color: var(--text);
      overflow: hidden;
    }
    .d-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.25);
      flex: none;
    }
    .d-title {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .d-title .id {
      color: var(--amber);
      font-size: var(--fs-lg);
      letter-spacing: 0.04em;
    }
    .d-sub {
      display: flex;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
    }
    .d-sub .iss {
      color: var(--cyan);
      font-size: var(--fs-sm);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .d-sub .st {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .d-meta {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: none;
    }
    .d-meta .nd {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.12em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    .d-route {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .d-route .rn {
      color: var(--cyan);
      font-size: var(--fs-xs);
      max-width: 12ch;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .act.danger {
      color: var(--red);
      border-color: var(--red-dim);
    }
    .act.danger:hover {
      border-color: var(--red);
      background: color-mix(in srgb, var(--red) 12%, transparent);
    }
    /* The detach failure reads between the header and the columns — the action's own
       result, next to the control that fired it, not buried in a column. */
    .detach-notice {
      margin: 6px;
      flex: none;
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
    /* Three columns of roughly equal weight: the work item, the path the chunk took,
       and what that produced (plus anything it is waiting on a human for). */
    .d-body {
      flex: 1;
      min-height: 0;
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 1px;
      background: var(--line);
      overflow: hidden;
    }
    .d-sec {
      background: var(--panel-deep);
      padding: 6px 8px;
      overflow-y: auto;
      min-height: 0;
      min-width: 0;
    }
    .awaiting,
    .escalation,
    .arts {
      margin-bottom: 8px;
    }
    .s-head {
      margin-bottom: 6px;
    }
    .none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
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
    .chip {
      border: 1px solid var(--line);
      color: var(--amber-hi);
      font-size: var(--fs-label);
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
      font-size: var(--fs-xs);
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
      font-size: var(--fs-sm);
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
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid var(--line);
      color: var(--amber-hi);
      padding: 4px 6px;
      font-size: var(--fs-sm);
    }
    /* One row per edge the chunk took: the attempt it happened on, the step itself,
       and the judgement that chose it — the mockup's history rows. */
    .timeline {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .step {
      display: grid;
      grid-template-columns: 16px 1fr auto;
      gap: 6px;
      align-items: baseline;
      padding: 3px 0;
      border-bottom: 1px solid var(--line);
      font-size: var(--fs-sm);
      line-height: 1.5;
    }
    .step .att {
      color: var(--label-dim);
      font-size: var(--fs-label);
    }
    .step .nd {
      display: flex;
      align-items: baseline;
      gap: 4px;
      min-width: 0;
      flex-wrap: wrap;
    }
    .step .from,
    .step .to {
      color: var(--text);
      text-transform: uppercase;
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
    }
    .step .arrow {
      color: var(--label-dim);
    }
    .step .jg {
      color: var(--amber);
      font-size: var(--fs-label);
      letter-spacing: 0.12em;
      text-transform: uppercase;
      white-space: nowrap;
    }
    /* A fail is the one judgement that means the chunk looped back rather than
       moved on, so it reads in the alarm color rather than the pass amber. */
    .step[data-choice='fail'] .jg {
      color: var(--red);
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
      font-size: var(--fs-xs);
    }
    .a-kind {
      color: var(--label-dim);
      font-size: var(--fs-label);
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
      font-size: var(--fs-sm);
    }
    .a-ref {
      margin-top: 4px;
      color: var(--label-dim);
      font-size: var(--fs-xs);
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
    /* The chunk's own facts, under the work item it serves. */
    .kv {
      display: grid;
      grid-template-columns: 74px 1fr;
      gap: 2px 8px;
      margin: 8px 0 0;
      font-size: var(--fs-sm);
    }
    .kv dt {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      align-self: center;
    }
    .kv dd {
      margin: 0;
      color: var(--amber);
      overflow-wrap: anywhere;
    }
    .i-title {
      margin: 2px 0 6px;
      color: var(--text);
      font-size: var(--fs-sm);
      line-height: 1.45;
    }
    .notice {
      margin: 0;
      padding: 4px 6px;
      border: 1px solid var(--red-dim);
      border-left-width: 2px;
      background: rgba(0, 0, 0, 0.2);
      color: var(--red);
      font-size: var(--fs-xs);
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
      font-size: var(--fs-sm);
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
      font-size: var(--fs-sm);
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
      font-size: var(--fs-sm);
    }
  `,
})
export class ChunkDetailPanel {
  /** The chunk aggregate to render (status, current node, history, artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's related PM items + fetch state, rendered by the Issue tab (issue #24).
   * Defaults to `loading` so the panel constructs without the container wiring it. */
  readonly pmItems = input<PmItemsState>({ status: 'loading', items: [] });

  /** The container's last detach failure for this chunk (the 409/404 surfaced, not
   * swallowed — issue #42), or `null` when there is nothing to report. */
  readonly detachError = input<string | null>(null);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** Emitted when the operator answers an open question (MVP criterion 7). */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Emitted with the chunk id when the operator confirms Detach (D-088, issue #42). */
  readonly detach = output<string>();

  /** The chunk's short name — the same identity its board card carries. */
  protected readonly shortId = computed<string>(() => shortChunkId(this.detail().chunk_id));

  /** The chunk's PM work item — the label its board card carries; empty when unlabeled. */
  protected readonly pointerLabel = computed<string>(() =>
    (this.detail().pm_pointers ?? []).flatMap((p) => (p.label ? [p.label] : [])).join(' '),
  );

  /** The chunk's PM pointer count — legible before the forge read lands. */
  protected readonly pointerCount = computed<number>(() => this.detail().pm_pointers?.length ?? 0);

  /** The runner currently holding the chunk's route, or `—` when nothing holds it —
   * the work-item column's plain-fact reading of the same {@link route} the header's
   * Detach control acts on. */
  protected readonly agent = computed<string>(() => this.route()?.runner_id ?? '—');

  /**
   * How many attempts the chunk has taken. The epoch is incremented per work
   * attempt (D-011), so the latest epoch *is* the attempt count — a chunk that has
   * never been worked has no epoch yet and reads `—` rather than a misleading `0`.
   */
  protected readonly attempts = computed<string>(() => {
    const epoch = this.detail().latest_epoch;
    return epoch === null || epoch === undefined ? '—' : String(epoch);
  });

  /** Transient "Copied" state for the takeover-command copy button. */
  protected readonly copied = signal(false);

  protected readonly history = computed<readonly TransitionView[]>(() => this.detail().history ?? []);
  protected readonly artifacts = computed<readonly ArtifactView[]>(() => this.detail().artifacts ?? []);

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

  /** The chunk's live route, if any — Detach shows only while this is non-null
   * (D-088, issue #42): a chunk with no live route has nothing to release. */
  protected readonly route = computed<RouteView | null>(() => this.detail().route ?? null);

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

  /** Confirm, then emit `detach` for the container's mutation to fire. A
   * native `confirm()` — the same one-line browser affordance `copyTakeover` already
   * reaches for — guards the forcible release; declining emits nothing. */
  protected onDetach(): void {
    if (!this.route()) return;
    const confirmed = globalThis.confirm(
      `Detach chunk ${this.detail().chunk_id} from its runner? This releases the runner; ` +
        `the chunk keeps its current status (this is not requeue).`,
    );
    if (!confirmed) return;
    this.detach.emit(this.detail().chunk_id);
  }
}
