import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { GraphSummaryView } from '../api/hub';
import { KitPanel } from '../kit/kit-panel';
import { injectHubGraphsQuery } from './graphs.query';

/** One graph name's lineage, grouped client-side from the flat `GraphSummaryView[]`
 * the hub serves in `created_at DESC` order. */
interface GraphGroup {
  readonly name: string;
  /** The lineage, newest-first (the order the list already arrives in). */
  readonly versions: readonly GraphSummaryView[];
  /** The one version in the group with `effective: true`. */
  readonly effective: GraphSummaryView;
}

/**
 * The graph explorer's **list** panel — every minted graph, grouped by name (the
 * primary object; a name is a lineage of immutable versions). Each group shows its
 * version count and the effective version's summary; expanding a group reveals the
 * full lineage newest-first, each row carrying its `graph_id`, `created_at`, and an
 * **effective** / **superseded** / **retired** badge — the `graphs` row itself is
 * never mutated (still insert-only), the marker is the `effective`/`retired` facts
 * `GET /api/graphs` derives (issue #101's reversible lifecycle brake, layered on top
 * of the pre-#101 `effective` derivation). Any version, effective, superseded, or
 * retired, is selectable and opens identically (`selectGraph`); retiring/re-enabling
 * itself is driven from the detail view (`graph-detail.ts`), not this list. Follows
 * `queue-panel.ts`: a standalone `fleet-`prefixed, OnPush container over the
 * generated client (bzh:generated-client) via TanStack Query.
 */
@Component({
  selector: 'fleet-graph-explorer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel],
  template: `
    <fleet-kit-panel class="graph-explorer" aria-label="Graphs" data-testid="graph-explorer" label="Graphs · by name">
      @if (graphsQuery.isPending()) {
        <p class="none" data-testid="graph-explorer-loading">Loading graphs…</p>
      } @else if (graphsQuery.isError()) {
        <p class="none" data-testid="graph-explorer-error">Failed to load graphs.</p>
      } @else if (groups().length === 0) {
        <p class="none" data-testid="graph-explorer-empty">No graphs minted yet.</p>
      } @else {
        <ul class="groups" data-testid="graph-explorer-groups">
          @for (group of groups(); track group.name) {
            <li class="group" data-testid="graph-explorer-group" [attr.data-name]="group.name">
              <button
                type="button"
                class="group-head"
                data-testid="graph-explorer-group-toggle"
                (click)="toggle(group.name)"
              >
                <span class="name">{{ group.name }}</span>
                <span class="count" data-testid="graph-explorer-group-count">{{ group.versions.length }} version{{
                  group.versions.length === 1 ? '' : 's'
                }}</span>
                <span class="summary" data-testid="graph-explorer-group-effective">{{
                  group.effective.graph_id
                }}</span>
              </button>
              @if (isExpanded(group)) {
                <ol class="lineage" data-testid="graph-explorer-lineage">
                  @for (version of group.versions; track version.graph_id) {
                    <li class="row-item">
                      <button
                        type="button"
                        class="row"
                        data-testid="graph-explorer-row"
                        [class.selected]="version.graph_id === selectedGraphId()"
                        [attr.data-graph-id]="version.graph_id"
                        (click)="selectGraph.emit(version.graph_id)"
                      >
                        <span class="gid" data-testid="graph-explorer-graph-id">{{ version.graph_id }}</span>
                        <span class="created" data-testid="graph-explorer-created-at">{{ version.created_at }}</span>
                        <span
                          class="badge"
                          data-testid="graph-explorer-badge"
                          [class.effective]="version.effective"
                          [class.retired]="version.retired"
                          [class.superseded]="!version.effective && !version.retired"
                          >{{ versionLabel(version) }}</span
                        >
                      </button>
                    </li>
                  }
                </ol>
              }
            </li>
          }
        </ul>
      }
    </fleet-kit-panel>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-size: var(--fs-base);
      color: var(--text);
    }
    .none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      padding: 6px 8px;
    }
    .groups {
      list-style: none;
      margin: 0;
      padding: 4px;
      display: flex;
      flex-direction: column;
      gap: 3px;
      overflow-y: auto;
    }
    .group-head {
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto 1fr;
      align-items: center;
      gap: 8px;
      padding: 3px 6px;
      border: 1px solid var(--line);
      background: var(--overlay-20);
      color: var(--text);
      font-family: inherit;
      font-size: var(--fs-sm);
      cursor: pointer;
      text-align: left;
    }
    .group-head:hover {
      border-color: var(--cyan);
    }
    .name {
      color: var(--cyan);
    }
    .count {
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    .summary {
      color: var(--label-dim);
      font-size: var(--fs-xs);
      overflow: hidden;
      text-overflow: ellipsis;
      text-align: right;
    }
    .lineage {
      list-style: none;
      margin: 0;
      padding: 2px 0 2px 12px;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .row {
      width: 100%;
      display: grid;
      grid-template-columns: 1fr auto auto;
      align-items: center;
      gap: 8px;
      padding: 2px 6px;
      border: 1px solid var(--line);
      background: transparent;
      color: var(--text);
      font-family: inherit;
      cursor: pointer;
      font-size: var(--fs-xs);
      text-align: left;
    }
    .row:hover {
      border-color: var(--cyan);
    }
    .row.selected {
      border-color: var(--amber-hi);
    }
    .gid {
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .created {
      color: var(--label-dim);
    }
    .badge {
      padding: 1px 6px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 0.85em;
    }
    .badge.effective {
      color: var(--cyan);
      border: 1px solid var(--cyan);
    }
    .badge.superseded {
      color: var(--label-dim);
      border: 1px solid var(--line);
    }
    .badge.retired {
      color: var(--red);
      border: 1px solid var(--red);
    }
  `,
})
export class GraphExplorer {
  protected readonly graphsQuery = injectHubGraphsQuery();

  /** The currently open detail's graph id, or `null` — highlights its row. */
  readonly selectedGraphId = input<string | null>(null);

  /** Emitted with the `graph_id` of a clicked row, effective or superseded alike. */
  readonly selectGraph = output<string>();

  /** Group names the operator has expanded. */
  private readonly expandedNames = signal<ReadonlySet<string>>(new Set());

  /** Every graph grouped by name; each group's lineage preserves the hub's
   * `created_at DESC` order (never re-derived client-side — the `effective`
   * flag and the ordering are both trusted from the server). */
  protected readonly groups = computed<readonly GraphGroup[]>(() => {
    const list = this.graphsQuery.data() ?? [];
    const byName = new Map<string, GraphSummaryView[]>();
    for (const summary of list) {
      const versions = byName.get(summary.name);
      if (versions) versions.push(summary);
      else byName.set(summary.name, [summary]);
    }
    return Array.from(byName.entries()).map(([name, versions]) => ({
      name,
      versions,
      effective: versions.find((v) => v.effective) ?? versions[0],
    }));
  });

  protected toggle(name: string): void {
    this.expandedNames.update((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  /** A group is expanded either because the operator toggled it open, or because
   * it holds the currently selected/deep-linked version — so navigating straight
   * to a superseded version's detail still reveals its row in the list. */
  protected isExpanded(group: GraphGroup): boolean {
    if (this.expandedNames().has(group.name)) return true;
    const selected = this.selectedGraphId();
    return selected !== null && group.versions.some((v) => v.graph_id === selected);
  }

  /** `effective` takes precedence (a graph can be both, briefly nonsensical, only if
   * the wire ever disagreed with itself); otherwise `retired` names issue #101's own
   * lifecycle state distinctly from "merely superseded by a newer version". */
  protected versionLabel(version: GraphSummaryView): string {
    if (version.effective) return 'effective';
    if (version.retired) return 'retired';
    return 'superseded';
  }
}
