import { ChangeDetectionStrategy, Component, TemplateRef, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ChunkDetail, ChunkNeighborView, RouteView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitButton } from '../kit/kit-button';
import { KitFactList, type KitFact } from '../kit/kit-fact-list';
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
  imports: [KitButton, KitFactList, RouterLink],
  templateUrl: './chunk-facts.html',
  styleUrl: './chunk-facts.css',
})
export class ChunkFacts {
  /** The chunk aggregate to render (status, node, route, epoch, graph). */
  readonly detail = input.required<ChunkDetail>();

  /** Whether the current identity may set the chunk's graph (`chunk:control` —
   * issue #210). Withholds the edit row when `false`, alongside {@link editable};
   * `null`/pending resolves to `false` (hidden until confirmed). */
  readonly canControl = input(false);

  /** The graphs view's own path segments, before the graph id — when set, the Graph
   * row's value links there (`/graphs/:graphId`, `graphs-page.ts`) so the operator can
   * jump straight from a chunk to its pinned graph's structure. `null` (the default)
   * withholds the link and falls back to today's plain-text value, since this
   * component is shared with the runner app, which has no `/graphs` route at all to
   * point at (unlike the hub's own `board/chunk` path both apps share) — a consumer
   * that does have somewhere to send the operator opts in explicitly
   * (`ChunkDetailHeader.linkBase` follows the same route-address-from-outside
   * convention, for the same cross-app reason). */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** The chunk detail route's own path segments, before a neighbor's chunk id — the
   * Depends on / Blocks rows link each neighbor there. Non-nullable with a default, the
   * same shape `ChunkDetailHeader.linkBase` carries: unlike `/graphs` both apps have
   * this route. */
  readonly chunkLinkBase = input<readonly string[]>(['/board', 'chunk']);

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
   * that always 409s (`blizzard-context:/domain/work/migration.md` `bzh:migration-not-transition`). */
  protected readonly editable = computed<boolean>(() => {
    const detail = this.detail();
    const unclaimed = detail.status === 'not_ready' || detail.status === 'ready';
    return unclaimed && !detail.current_node_id;
  });

  /** The chunk's standing dependency edges, each direction its own fact row — the
   * fact table is where they read best: one shared label column, so the neighbors line
   * up under the same right-hand edge as Status, Node, and Graph rather than sitting in
   * a block of their own with its own alignment. A direction with no edges renders no
   * row at all, the same way Model's retired row simply is not there. */
  protected readonly prerequisites = computed<readonly ChunkNeighborView[]>(
    () => this.detail().neighborhood?.prerequisites ?? [],
  );

  protected readonly dependents = computed<readonly ChunkNeighborView[]>(
    () => this.detail().neighborhood?.dependents ?? [],
  );

  /** A neighbor's compact ref — every surface that names an entity compactly resolves
   * through {@link compactRef} (`compact-ref.ts`). */
  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** The neighbor's own derived status, or `unknown` for the residual race a neighbor's
   * facts fail to resolve (`status: null`). */
  protected statusLabel(neighbor: ChunkNeighborView): string {
    return neighbor.status ?? 'unknown';
  }

  /** Emit a graph repin — no-op on a blank id (issue #27). */
  protected submitGraph(graphId: string): void {
    const trimmed = graphId.trim();
    if (!trimmed) return;
    this.editGraph.emit({ chunkId: this.detail().chunk_id, graphId: trimmed });
  }

  /** The node fact's rendered value — the current node's name, falling back to its
   * bare id, then `—` for a chunk that has not yet reached one. */
  protected readonly nodeLabel = computed<string>(
    () => this.detail().current_node_name ?? this.detail().current_node_id ?? '—',
  );

  /** The identity table's rows — a method, not a stored computed, since the Graph,
   * Depends on, and Blocks rows' markup needs the `<ng-template>`s the view declares
   * for them (`KitFactList`'s own templated-row contract). Depends on / Blocks are
   * appended only when their direction has an edge — a direction with none renders no
   * row at all, the same way Model's retired row simply is not there. */
  protected factRows(
    graphValue: TemplateRef<unknown>,
    dependsOnValue: TemplateRef<unknown>,
    blocksValue: TemplateRef<unknown>,
  ): readonly KitFact[] {
    const rows: KitFact[] = [
      { label: 'Status', value: this.detail().status, testid: 'fact-status' },
      { label: 'Node', value: this.nodeLabel(), testid: 'fact-node' },
      { label: 'Runner', value: this.runner(), testid: 'fact-runner' },
      { label: 'Attempts', value: this.attempts(), testid: 'fact-attempts' },
      { label: 'Graph', template: graphValue, testid: 'fact-graph' },
    ];
    if (this.prerequisites().length) rows.push({ label: 'Depends on', template: dependsOnValue, testid: 'fact-depends-on' });
    if (this.dependents().length) rows.push({ label: 'Blocks', template: blocksValue, testid: 'fact-blocks' });
    return rows;
  }
}
