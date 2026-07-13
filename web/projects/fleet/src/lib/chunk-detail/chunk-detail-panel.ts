import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ArtifactView, ChunkDetail, TransitionView } from '../api/hub';

/**
 * The chunk detail drawer — a chunk's node history and its artifact store (D-036,
 * MVP criterion 9/11). Slides over the board when a card is selected and renders:
 *
 * - the **transition history**, oldest-first: every edge the chunk took, so the
 *   review-fail loop back to build reads as a visible `review → build (fail)` step;
 * - the **artifact store**: each entry keyed `{node}.{artifact-name}.{epoch}`, with an
 *   **asset's** findings text shown inline (the review notes a fail carried back) and a
 *   **git_commit's** pinned `repo @ commit` reference (the branch pointers merged).
 *
 * Presentational only: it holds the detail input and emits `dismiss`; the data client
 * lives in the query. All color comes from the design-token layer, never hard-coded.
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

  protected readonly history = computed<readonly TransitionView[]>(() => this.detail().history ?? []);
  protected readonly artifacts = computed<readonly ArtifactView[]>(() => this.detail().artifacts ?? []);
}
