import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type {
  ArtifactView,
  ChunkDetail,
  ChunkStatus,
  DecisionView,
  EscalationView,
  PauseView,
  PmItemEntry,
  QuestionView,
  RouteView,
  TransitionView,
} from '../api/hub';
import { shortChunkId } from '../chunk-id';

/** Statuses the hub's `PauseService` refuses to pause (`ChunkNotPausable`), mirrored
 * here so the dock never offers a Pause the server would answer with a 409 (issue #46).
 * A terminal or mid-delivery chunk has no work to stop.
 *
 * `paused` is deliberately **absent**: whether a chunk is already paused is not a
 * question `status` can answer (PAUSED derives below the human-gated states), so it is
 * never asked here — see `ChunkDetailPanel.pause`, which owns that half by reading the
 * fact. */
const NOT_PAUSABLE = new Set<ChunkStatus>(['done', 'stopped', 'delivering']);

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

/** Emitted when the operator repins a not-ready chunk's graph from the dock (issue #27). */
export interface EditGraphEvent {
  readonly chunkId: string;
  readonly graphId: string;
}

/** Emitted when the operator repins a not-ready chunk's model from the dock (issue #27). */
export interface EditModelEvent {
  readonly chunkId: string;
  readonly model: string;
}

/**
 * The chunk detail dock (MVP criterion 9/11) — everything known about the
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
 * The **Pause/Resume** control sits in that same header beside Detach, following the
 * same pattern (issue #46): pause keeps the claim and kills the worker, where detach
 * gives the claim up — the two halves of one choice, so they read together. Which of
 * the two renders is switched on the pause **fact** (`ChunkDetail.pause`), never on
 * `status` — see `pause` below.
 *
 * Under the header sit three columns of roughly equal weight — **what it is**,
 * **where it has been**, **what it produced**:
 *
 * - **the work item** — the chunk's PM pointers, each a link out to the forge (the
 *   board cards deliberately carry none), the issue title and body pass-through
 *   (issue #24), and the chunk's own facts: status, node, agent, attempts, and its
 *   pinned **graph** and **model** — each editable inline, text-input-and-Set, the
 *   same shape the open-question Answer control already uses, while the chunk sits
 *   `not_ready` (issue #27). The edit row is gated on {@link editable} — the fact,
 *   not a confirm — so the control simply disappears once the chunk leaves
 *   `not_ready` rather than staying up to fail a 409, mirroring how Pause and Detach
 *   already withhold themselves from a refusal the server would answer with one.
 *   No graph picker: the hub exposes no `list` route over graphs (only `POST
 *   /api/graphs` to mint one), so the graph edit takes a graph id by hand, same as
 *   the model edit takes a model name by hand — both plain text, no listing to pick
 *   from either;
 * - **node history**, oldest-first: every edge the chunk took with the judgement
 *   that chose it, so the review-fail loop back to build reads as a visible
 *   `review → build (fail)` step;
 * - **artifacts and asks** — the **artifact store**, each entry keyed
 *   `{node}.{artifact-name}.{epoch}`, with an **asset's** findings text inline and a
 *   **git_commit's** pinned `repo @ commit` reference; above it, whatever the chunk
 *   waits on a human for: an open **question** with an inline **Answer** action (MVP
 *   criterion 7), an open gate **decision** as **choice buttons** (MVP
 *   criterion 12), or an **escalation's** copyable **takeover command**.
 *
 * Presentational only: it holds the detail input and emits `dismiss`, `answerQuestion`,
 * `resolveDecision`, `detach`, `pauseChunk`, `resumeChunk`, `editGraph`, and
 * `editModel` (the route-releasing and worker-killing verbs guarded by a `confirm()`,
 * the one browser affordance this panel already reaches for — see `copyTakeover`; the
 * graph/model edits are not — repinning either before the chunk has run costs nothing
 * to undo, unlike detach or pause); the data client (the mutations those events drive,
 * and the error any of them surfaces back down as `actionError`) lives in the
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
          <!-- Who paused the chunk, read off the pause fact — the header states it because
               a paused chunk that is also parked on a question derives waiting_on_human, so
               the status cell above would never say so (issue #46). -->
          @if (pause(); as p) {
            <span class="pb" data-testid="chunk-pause-by">Paused by {{ p.by }}</span>
          }
          <!-- Pause/Resume sits beside Detach: the two are the halves of one choice —
               detach gives the claim away, pause keeps it. Unlike Detach it does not hang
               off the route, so it renders whether or not a route is live (issue #46). -->
          <div class="d-actions">
            @if (pause()) {
              <button
                type="button"
                class="act"
                data-testid="resume-chunk"
                [attr.aria-label]="'Resume chunk ' + detail().chunk_id"
                (click)="onResume()"
              >
                Resume
              </button>
            } @else if (pausable()) {
              <button
                type="button"
                class="act"
                data-testid="pause-chunk"
                [attr.aria-label]="'Pause chunk ' + detail().chunk_id"
                (click)="onPause()"
              >
                Pause
              </button>
            }
          </div>
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

      @if (actionError(); as err) {
        <p class="notice action-notice" data-testid="action-error" role="alert">{{ err }}</p>
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
            <!-- Graph and model are always shown; the edit row only while the chunk is
                 still not_ready (issue #27) — the same shape as the open-question
                 answer control, gated on the fact rather than confirmed. -->
            <dt>Graph</dt>
            <dd data-testid="fact-graph">
              <span data-testid="graph-value" [title]="detail().graph_id">{{ detail().graph_id }}</span>
              @if (editable()) {
                <span class="edit-row">
                  <input
                    #graphInput
                    class="edit-input"
                    type="text"
                    data-testid="graph-input"
                    placeholder="New graph id…"
                    [attr.aria-label]="'Set graph for chunk ' + detail().chunk_id"
                  />
                  <button
                    type="button"
                    class="act"
                    data-testid="graph-submit"
                    (click)="submitGraph(graphInput.value); graphInput.value = ''"
                  >
                    Set
                  </button>
                </span>
              }
            </dd>
            <dt>Model</dt>
            <dd data-testid="fact-model">
              <span data-testid="model-value">{{ detail().model }}</span>
              @if (editable()) {
                <span class="edit-row">
                  <input
                    #modelInput
                    class="edit-input"
                    type="text"
                    data-testid="model-input"
                    placeholder="New model…"
                    [attr.aria-label]="'Set model for chunk ' + detail().chunk_id"
                  />
                  <button
                    type="button"
                    class="act"
                    data-testid="model-submit"
                    (click)="submitModel(modelInput.value); modelInput.value = ''"
                  >
                    Set
                  </button>
                </span>
              }
            </dd>
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
    /* The pause marker reads amber — an operator-held state, not a fault. */
    .d-meta .pb {
      color: var(--amber-hi);
      font-size: var(--fs-xs);
      white-space: nowrap;
    }
    .d-route,
    .d-actions {
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
    /* An operator action's failure reads between the header and the columns — the
       action's own result, next to the control that fired it, not buried in a column.
       One notice serves detach, pause, and resume alike. */
    .action-notice {
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
    /* The graph/model edit row (issue #27) — the same input-plus-act shape as
       .answer-row, scaled down to sit inside a .kv fact cell. */
    .edit-row {
      display: flex;
      gap: 4px;
      margin-top: 3px;
    }
    .edit-input {
      flex: 1;
      min-width: 0;
      font-family: inherit;
      font-size: var(--fs-xs);
      background: rgba(0, 0, 0, 0.35);
      border: 1px solid var(--line);
      color: var(--text);
      padding: 2px 4px;
    }
    .edit-input:focus-visible {
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
       and the judgement that chose it. */
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

  /** The container's last **operator-action** failure for this chunk (the 409/404
   * surfaced, not swallowed — issue #42), or `null` when there is nothing to report.
   * One notice for every action in this dock (detach, pause, resume): they share a
   * surface because only one of them can be in flight from one operator at a time,
   * and a second banner would only ever compete with the first for the same corner. */
  readonly actionError = input<string | null>(null);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** Emitted when the operator answers an open question (MVP criterion 7). */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Emitted with the chunk id when the operator confirms Detach (issue #42). */
  readonly detach = output<string>();

  /** Emitted with the chunk id when the operator confirms Pause (issue #46). Named
   * `pauseChunk`, not `pause` — `@angular-eslint/no-output-native` forbids an output
   * shadowing the native DOM `pause` event. */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Resume (issue #46). Named
   * `resumeChunk` for symmetry with `pauseChunk`. */
  readonly resumeChunk = output<string>();

  /** Emitted when the operator sets a not-ready chunk's graph from the facts column
   * (issue #27). No confirm — see the class doc. */
  readonly editGraph = output<EditGraphEvent>();

  /** Emitted when the operator sets a not-ready chunk's model from the facts column
   * (issue #27). No confirm — see the class doc. */
  readonly editModel = output<EditModelEvent>();

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
   * attempt, so the latest epoch *is* the attempt count — a chunk that has
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
   * (issue #42): a chunk with no live route has nothing to release. */
  protected readonly route = computed<RouteView | null>(() => this.detail().route ?? null);

  /** The chunk's open operator pause, if any — who set it (issue #46). Read off the
   * detail's `pause` fact, not `status`: a chunk both paused and parked on a question
   * derives `waiting_on_human`, so `status` alone would never surface it. Only the
   * detail carries this — `ChunkSummary` (the board card) does not, so this renders
   * in the dock rather than on the card.
   *
   * This is also the **Pause/Resume switch**: non-null renders Resume, null renders
   * Pause (subject to {@link pausable}). Keying that switch on the fact rather than
   * on `status === 'paused'` is the whole reason the action lives here — a status-keyed
   * dock would offer a paused-and-asking chunk PAUSE (a no-op re-pause) and never
   * RESUME, leaving it un-pausable from the board while this very line says who paused
   * it. `status` must never gate Resume. */
  protected readonly pause = computed<PauseView | null>(() => this.detail().pause ?? null);

  /** Whether an **unpaused** chunk may be paused — mirrors the hub `PauseService`'s
   * refusal (`ChunkNotPausable`) so the dock never offers a control the server would
   * answer with a 409 (issue #46), exactly as Detach shows only with a live route to
   * release (issue #42). `waiting_on_human`/`needs_human` are deliberately pausable —
   * the lever stays broad by decision.
   *
   * Status is a sound input **here** and only here: this asks "would the server refuse
   * a pause?", which is a question about the chunk's terminal-or-delivering state, and
   * those statuses hide nothing. It is *not* asked "is this already paused?" — that is
   * {@link pause}'s job, and the reason this predicate is consulted only on the Pause
   * half of the switch. */
  protected readonly pausable = computed<boolean>(() => !NOT_PAUSABLE.has(this.detail().status));

  /** Whether the chunk's graph and model may be edited — `not_ready` only (issue #27):
   * `EditService` refuses both edits 409 the moment a chunk is promoted, claimed, or
   * later, so the facts column withholds the edit row rather than let an operator
   * hit that refusal. Unlike {@link pausable}, there is no separate fact to key this
   * on — `not_ready` is itself the whole window. */
  protected readonly editable = computed<boolean>(() => this.detail().status === 'not_ready');

  /** Emit an answer for a question — no-op on an empty answer. */
  protected submitAnswer(questionId: string, answer: string): void {
    const trimmed = answer.trim();
    if (!trimmed) return;
    this.answerQuestion.emit({ questionId, answer: trimmed, chunkId: this.detail().chunk_id });
  }

  /** Emit a graph repin — no-op on a blank id (issue #27). */
  protected submitGraph(graphId: string): void {
    const trimmed = graphId.trim();
    if (!trimmed) return;
    this.editGraph.emit({ chunkId: this.detail().chunk_id, graphId: trimmed });
  }

  /** Emit a model repin — no-op on a blank name (issue #27). */
  protected submitModel(model: string): void {
    const trimmed = model.trim();
    if (!trimmed) return;
    this.editModel.emit({ chunkId: this.detail().chunk_id, model: trimmed });
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

  /** Confirm, then emit `pauseChunk` for the container's mutation to fire (issue #46).
   * Same `confirm()` guard as `onDetach` — a pause kills the chunk's running worker. */
  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const confirmed = globalThis.confirm(
      `Pause chunk ${this.detail().chunk_id}? This kills its active worker but keeps the ` +
        `claim (this is not detach); resume it later to pick the work back up.`,
    );
    if (!confirmed) return;
    this.pauseChunk.emit(this.detail().chunk_id);
  }

  /** Confirm, then emit `resumeChunk` for the container's mutation to fire (issue #46).
   * Guarded on the pause **fact**, never on `status`: the chunk this resumes may well
   * read `waiting_on_human` rather than `paused`. */
  protected onResume(): void {
    if (!this.pause()) return;
    const confirmed = globalThis.confirm(
      `Resume chunk ${this.detail().chunk_id}? Its runner picks the work back up from ` +
        `where the pause stopped it.`,
    );
    if (!confirmed) return;
    this.resumeChunk.emit(this.detail().chunk_id);
  }
}
