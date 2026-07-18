import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkDetail, RouteView } from '../api/hub';
import { KitButton } from '../kit/kit-button';

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
 * The chunk's own facts (issue #79) — the fixed-height glance a long issue
 * body must not scroll away: status, node, runner, attempts, and its pinned
 * **graph** and **model** — each editable inline, text-input-and-Set, while
 * the chunk sits `not_ready` (issue #27). The edit row is gated on
 * {@link editable} — the fact, not a confirm — so the control simply
 * disappears once the chunk leaves `not_ready` rather than staying up to
 * fail a 409.
 *
 * Projects {@link ChunkTokenBreakdown}'s cost/tokens rows into the `[token-breakdown]`
 * slot between Attempts and Graph, so the two components share one continuous
 * `<dl class="kv">` — the exact row order and grid the monolith rendered.
 */
@Component({
  selector: 'fleet-chunk-detail-facts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  template: `
    <dl class="kv" data-testid="chunk-facts">
      <dt>Status</dt>
      <dd data-testid="fact-status">{{ detail().status }}</dd>
      <dt>Node</dt>
      <dd data-testid="fact-node">{{ detail().current_node_name ?? detail().current_node_id ?? '—' }}</dd>
      <dt>Runner</dt>
      <dd data-testid="fact-runner">{{ runner() }}</dd>
      <dt>Attempts</dt>
      <dd data-testid="fact-attempts">{{ attempts() }}</dd>
      <ng-content select="[token-breakdown]" />
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
            <fleet-kit-button testid="graph-submit" (click)="submitGraph(graphInput.value); graphInput.value = ''">
              Set
            </fleet-kit-button>
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
            <fleet-kit-button testid="model-submit" (click)="submitModel(modelInput.value); modelInput.value = ''">
              Set
            </fleet-kit-button>
          </span>
        }
      </dd>
    </dl>
  `,
  styles: `
    :host {
      display: block;
    }
    /* The chunk's own facts, above the work item it serves — the fixed-height glance
       before the arbitrarily long issue body. */
    .kv {
      display: grid;
      grid-template-columns: 74px 1fr;
      gap: 2px 8px;
      margin: 0 0 8px;
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
    /* The graph/model edit row (issue #27) — the same input-plus-act shape as the
       awaiting-human answer row, scaled down to sit inside a .kv fact cell. */
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
      background: var(--overlay-35);
      border: 1px solid var(--line);
      color: var(--text);
      padding: 2px 4px;
    }
    .edit-input:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: 0;
    }
  `,
})
export class ChunkFacts {
  /** The chunk aggregate to render (status, node, route, epoch, graph, model). */
  readonly detail = input.required<ChunkDetail>();

  /** Emitted when the operator sets a not-ready chunk's graph (issue #27). No
   * confirm — repinning either before the chunk has run costs nothing to undo. */
  readonly editGraph = output<EditGraphEvent>();

  /** Emitted when the operator sets a not-ready chunk's model (issue #27). */
  readonly editModel = output<EditModelEvent>();

  /** The chunk's live route, read here as a plain fact — the same route the
   * header's Detach control acts on. */
  private readonly route = computed<RouteView | null>(() => this.detail().route ?? null);

  /** The runner currently holding the chunk's route, or `—` when nothing holds it. */
  protected readonly runner = computed<string>(() => this.route()?.runner_id ?? '—');

  /**
   * How many attempts the chunk has taken. The epoch is incremented per work
   * attempt, so the latest epoch *is* the attempt count — a chunk that has
   * never been worked has no epoch yet and reads `—` rather than a misleading `0`.
   */
  protected readonly attempts = computed<string>(() => {
    const epoch = this.detail().latest_epoch;
    return epoch === null || epoch === undefined ? '—' : String(epoch);
  });

  /** Whether the chunk's graph and model may be edited — `not_ready` only (issue #27):
   * `EditService` refuses both edits 409 the moment a chunk is promoted, claimed, or
   * later, so the facts column withholds the edit row rather than let an operator
   * hit that refusal. */
  protected readonly editable = computed<boolean>(() => this.detail().status === 'not_ready');

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
}
