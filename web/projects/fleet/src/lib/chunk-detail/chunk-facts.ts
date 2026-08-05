import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkDetail, RouteView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitButton } from '../kit/kit-button';
import { formatUtcYmd } from '../when';

/** Emitted when the operator repins a not-ready chunk's graph from the dock (issue #27). */
export interface EditGraphEvent {
  readonly chunkId: string;
  readonly graphId: string;
}

/**
 * The chunk's own facts (issue #79) — the fixed-height glance a long issue
 * body must not scroll away: status, node, runner, attempts, and its pinned
 * **graph**, editable inline (text-input-and-Set) while the chunk is unclaimed and has
 * not yet moved (issue #27, widened by #120, narrowed by #271). The edit row is gated
 * on {@link editable} — the fact, not a confirm — so the control simply disappears once
 * the pin is the engine's rather than staying up to fail a 409.
 *
 * A **Model** row stood beside Graph with the same inline editor until issue #144
 * retired `Chunk.model` — a knob that never reached the envelope, so the board offered
 * an edit that changed nothing about what the fleet ran. Its replacements
 * (`default_model`/`default_effort`) deliberately have no web surface for now: they are
 * written with `blizzard hub chunk set --default-model/--default-effort` and read back
 * with `chunk show`.
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
      <!-- Graph is always shown; the edit row only while the chunk is still
           not_ready (issue #27) — the same shape as the open-question answer
           control, gated on the fact rather than confirmed. -->
      <dt>Graph</dt>
      <dd data-testid="fact-graph">
        <span data-testid="graph-value" [title]="detail().graph_id">{{ graphLabel() }}</span>
        @if (editable() && canControl()) {
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
    /* The graph edit row (issue #27) — the same input-plus-act shape as the
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
  /** The chunk aggregate to render (status, node, route, epoch, graph). */
  readonly detail = input.required<ChunkDetail>();

  /** Whether the current identity may set the chunk's graph (`chunk:control` —
   * issue #210). Withholds the edit row when `false`, alongside {@link editable};
   * `null`/pending resolves to `false` (hidden until confirmed). */
  readonly canControl = input(false);

  /** Emitted when the operator sets a not-ready chunk's graph (issue #27). No
   * confirm — repinning either before the chunk has run costs nothing to undo. */
  readonly editGraph = output<EditGraphEvent>();

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

  /** The Graph fact row's label (issue #102) — the pinned graph's {@link compactRef}
   * (`G-XXXX`), with the graph's `name` and `created_at` (as `YYYYMMDD`) appended as
   * `#<name>-<YYYYMMDD>` when both are present on the detail *and* `created_at` parses.
   * Either absent (an older payload) or unparseable (`formatUtcYmd` degrading to `''`)
   * degrades to the compact ref alone, never a dangling `#`/`-`. The full raw id stays
   * as the row's `title` tooltip, read straight off `detail().graph_id`. */
  protected readonly graphLabel = computed<string>(() => {
    const detail = this.detail();
    const ref = compactRef(detail.graph_id);
    if (!detail.graph_name) return ref;
    const ymd = formatUtcYmd(detail.graph_created_at);
    if (!ymd) return ref;
    return `${ref}#${detail.graph_name}-${ymd}`;
  });

  /** Whether the chunk's graph may be edited — mirrors `EditService.edit`'s own two
   * conditions (issue #27, widened by #120, narrowed by #271) rather than the status
   * half alone: unclaimed **and** never moved. A chunk detached mid-graph derives
   * `ready` again while standing on a node of its old graph, and re-pinning it there is
   * a migration's job, so the facts column withholds the row rather than offer an edit
   * that always 409s (`blizzard-context:/domain/work.md` `bzh:migration-not-transition`). */
  protected readonly editable = computed<boolean>(() => {
    const detail = this.detail();
    const unclaimed = detail.status === 'not_ready' || detail.status === 'ready';
    return unclaimed && !detail.current_node_id;
  });

  /** Emit a graph repin — no-op on a blank id (issue #27). */
  protected submitGraph(graphId: string): void {
    const trimmed = graphId.trim();
    if (!trimmed) return;
    this.editGraph.emit({ chunkId: this.detail().chunk_id, graphId: trimmed });
  }
}
