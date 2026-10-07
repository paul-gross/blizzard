import { ChangeDetectionStrategy, Component, TemplateRef, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ChunkDetail, ChunkNeighborView, RouteView } from '../../api/hub';
import { compactRef } from '../../core/compact-ref';
import { runnerDisplayName } from '../../core/runner-display-name';
import { KitButton } from '../../kit/kit-button';
import { KitFactList, type KitFact } from '../../kit/kit-fact-list';
import { formatUtcYmd } from '../../core/when';

/** Emitted when the operator repins a not-ready chunk's graph from the dock. */
export interface EditGraphEvent {
  readonly chunkId: string;
  readonly graphId: string;
}

/**
 * The chunk's own facts — the fixed-height glance a long issue
 * body must not scroll away: status, node, runner, attempts, and its pinned
 * **graph**, editable inline (text-input-and-Set) while the chunk is unclaimed and has
 * not yet moved. The edit row is gated
 * on {@link editable} — the fact, not a confirm — so the control simply disappears once
 * the pin is the engine's rather than staying up to fail a 409.

 *
 * Projects {@link ChunkTokenBreakdown}'s cost/tokens rows into the `[token-breakdown]`
 * slot between Attempts and Graph, so the two components share one continuous
 * `<dl class="kv">`.
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

  /** Whether the current identity may set the chunk's graph (`chunk:control`).
   * Withholds the edit row when `false`, alongside {@link editable};
   * `null`/pending resolves to `false` (hidden until confirmed). */
  readonly canControl = input(false);

  /** Whether a graph repin is in flight for this chunk; disables the Set button. */
  readonly graphPending = input(false);

  /** The graphs view's own path segments, before the graph id — when set, the Graph
   * row's value links there (`/graphs/:graphId`, `graphs-page.ts`) so the operator can
   * jump straight from a chunk to its pinned graph's structure. `null` (the default)
   * withholds the link and renders a plain-text value, for a host with no
   * graphs route to point at — a consumer that does have one opts in explicitly. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** The chunk detail route's own path segments, before a neighbor's chunk id — the
   * Depends on / Blocks rows link each neighbor there. Non-nullable with a default,
   * since both apps have this route. */
  readonly chunkLinkBase = input<readonly string[]>(['/board', 'chunk']);

  /** Emitted when the operator sets a not-ready chunk's graph. No
   * confirm — repinning either before the chunk has run costs nothing to undo. */
  readonly editGraph = output<EditGraphEvent>();

  /** The chunk's live route, read here as a plain fact. */
  private readonly route = computed<RouteView | null>(() => this.detail().route ?? null);

  /** The display name of the runner holding the chunk's route, or `—` when nothing holds it. */
  protected readonly runner = computed<string>(() => {
    const route = this.route();
    return route ? runnerDisplayName(route.runner_id, route.runner_name) : '—';
  });

  /**
   * How many attempts the chunk has taken. The epoch is incremented per work
   * attempt, so the latest epoch *is* the attempt count — a chunk that has
   * never been worked has no epoch yet and reads `—` rather than a misleading `0`.
   */
  protected readonly attempts = computed<string>(() => {
    const epoch = this.detail().latest_epoch;
    return epoch === null || epoch === undefined ? '—' : String(epoch);
  });

  /** The Graph fact row's label — the pinned graph's {@link compactRef}
   * (`G-XXXX`), with the graph's `name` and `created_at` (as `YYYYMMDD`) appended as
   * `#<name>-<YYYYMMDD>` when both are present on the detail *and* `created_at` parses.
   * Either absent or unparseable (`formatUtcYmd` degrading to `''`)
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

  /** Whether the chunk's graph may be edited — the wire's `graph_editable`: unclaimed **and**
   * never moved. A chunk detached mid-graph derives `ready` again while standing on a node of
   * its old graph, and re-pinning it there is a migration's job, so the facts column withholds
   * the row rather than offer an edit that always 409s
   * (`blizzard-context:/domain/work/migration.md` `bzh:migration-not-transition`). */
  protected readonly editable = computed<boolean>(() => this.detail().graph_editable ?? false);

  /** The chunk's standing dependency edges, each direction its own fact row. Placement
   * pinned by `chunk-facts-alignment.shell-sweep.spec.ts`'s "keeps a standing-edge row's
   * value column…" case; the edge-less-direction-renders-no-row behavior by
   * `chunk-facts.spec.ts`'s "renders the standing edges…" case. */
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

  /** Emit a graph repin — no-op on a blank id. */
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
   * appended only when their direction has an edge. */
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
