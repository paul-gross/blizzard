import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { GraphSummaryView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitPanel } from '../kit/kit-panel';
import { asyncState } from '../query-state';
import { FleetWhen } from '../when-display';
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

/** One group's operator-set expansion state, stamped with the selection that was
 * current when it was set — see `GraphExplorer.isExpanded` for how the stamp expires it. */
interface ExpansionOverride {
  readonly expanded: boolean;
  readonly selectionAtToggle: string | null;
}

/**
 * The graph explorer's **list** panel — every minted graph, grouped by name (the
 * primary object; a name is a lineage of immutable versions). Each group shows its
 * version count and the effective version's summary; expanding a group selects that
 * effective version (issue #152 — the detail opens on the first click) and reveals the
 * full lineage newest-first, each row carrying its `graph_id`, `created_at`, and an
 * **effective** / **superseded** / **retired** badge — the `graphs` row itself is
 * never mutated (still insert-only), the marker is the `effective`/`retired` facts
 * `GET /api/graphs` derives (issue #101's reversible lifecycle brake, layered on top
 * of the pre-#101 `effective` derivation). Any version, effective, superseded, or
 * retired, is selectable and opens identically (`selectGraph`); retiring/re-enabling
 * itself is driven from the detail view (`graph-detail.ts`), not this list. Follows
 * `runner-panel.ts`: a standalone `fleet-`prefixed, OnPush container over the
 * generated client (bzh:generated-client) via TanStack Query.
 */
@Component({
  selector: 'fleet-graph-explorer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel, FleetWhen],
  templateUrl: './graph-explorer.html',
  styleUrl: './graph-explorer.css',
})
export class GraphExplorer {
  protected readonly graphsQuery = injectHubGraphsQuery();

  /** The currently open detail's graph id, or `null` — highlights its row. */
  readonly selectedGraphId = input<string | null>(null);

  /** Emitted with the `graph_id` of a clicked row, effective or superseded alike — or
   * of a group's effective version when its header expands the group. */
  readonly selectGraph = output<string>();

  /** One group's explicit open/closed state, plus the selection that was current when
   * the operator set it. An *override*, not a plain expanded-set, because expansion is
   * otherwise derived from the selection ({@link isExpanded}) and a header click now
   * makes a selection — without a recorded `false`, collapsing a group the operator had
   * just expanded would be undone by the very selection that click emitted. The recorded
   * selection is what keeps the override from outliving its moment; see
   * {@link isExpanded}. */
  private readonly expansionOverrides = signal<ReadonlyMap<string, ExpansionOverride>>(new Map());

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

  /** This list's async state — derived straight from its own query, since it
   * has no container/presentational split of its own. */
  protected readonly state = computed<KitAsyncStateValue>(() =>
    asyncState(this.graphsQuery, this.groups().length === 0),
  );

  /** A header click expands the group **and** opens its effective version (issue #152)
   * — the header already displays that id, so requiring a second click on the version
   * row to see anything was pure friction. Collapsing only closes the group: the
   * selection is left exactly as it was, so the header click can only ever add a
   * selection, never clear or change one. */
  protected toggle(group: GraphGroup): void {
    const willExpand = !this.isExpanded(group);
    const override: ExpansionOverride = { expanded: willExpand, selectionAtToggle: this.selectedGraphId() };
    this.expansionOverrides.update((prev) => new Map(prev).set(group.name, override));
    if (willExpand) this.selectGraph.emit(group.effective.graph_id);
  }

  /** A group is expanded because the operator toggled it open, or — absent a live
   * toggle — because it holds the currently selected/deep-linked version, so navigating
   * straight to a superseded version's detail still reveals its row in the list.
   *
   * A toggle stays live only until a *new* selection lands inside that same group: that
   * navigation is a fresher statement of intent than the older click, so it supersedes
   * it and the selection-derived default takes over again. Without that expiry a single
   * collapse would suppress the reveal for the component's whole lifetime — the group
   * would stay shut even when deep-linked straight into one of its versions. The
   * selection a header click emits does *not* expire that click's own override: it is
   * recorded as `selectionAtToggle`, so an expand survives its own emission and a
   * collapse survives the selection it deliberately left in place. */
  protected isExpanded(group: GraphGroup): boolean {
    const selected = this.selectedGraphId();
    const holdsSelection = selected !== null && group.versions.some((v) => v.graph_id === selected);
    const override = this.expansionOverrides().get(group.name);
    if (override !== undefined && !(holdsSelection && selected !== override.selectionAtToggle)) {
      return override.expanded;
    }
    return holdsSelection;
  }

  /** The compact display id (issue #206) — the single owner of that rendering is
   * {@link compactRef}, kept behind this wrapper so the template calls a bound method
   * rather than an imported free function. */
  protected shortId(graphId: string): string {
    return compactRef(graphId);
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
