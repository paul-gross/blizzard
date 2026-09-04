import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import type { GraphSummaryView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitBadge } from '../kit/kit-badge';
import { KitChip } from '../kit/kit-chips';
import { KitSelectRow } from '../kit/kit-select-row';
import type { Tone } from '../kit/tone';
import { FleetWhen } from '../when-display';

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
 * current when it was set — see {@link GraphExplorerList.isExpanded} for how the
 * stamp expires it. */
interface ExpansionOverride {
  readonly expanded: boolean;
  readonly selectionAtToggle: string | null;
}

/** A version's lifecycle label, {@link GraphExplorerList.versionLabel}'s own return
 * union, kept local since nothing else in `graphs/` needs the wire's `effective`/
 * `retired` booleans folded this way. */
type VersionLabel = 'effective' | 'superseded' | 'retired';

/** A version's lifecycle label → badge tone — borrows the shared `Tone` ladder for
 * its color rather than inventing a graph-lifecycle-specific one
 * (`chunk-issue-list.ts`'s own `PRIORITY_TONE`/`events-view.ts`'s `SEVERITY_TONE`
 * shape). The mapping is chosen for the color each label already carried in the
 * hand-rolled `.badge.effective`/`.badge.superseded`/`.badge.retired` chrome this
 * replaces, not for `Tone`'s own documented meanings: `effective` reuses
 * `spawning`'s cyan (`Tone`'s only cyan), `superseded` reuses `idle`'s dim — a
 * reasonable double meaning, since a superseded version really is this lineage's
 * spent, inert entry — and `retired` reuses `stale`'s red, reading as the alarm a
 * deliberately disabled version (issue #101) should. */
const LIFECYCLE_TONE: Readonly<Record<VersionLabel, Tone>> = {
  effective: 'spawning',
  superseded: 'idle',
  retired: 'stale',
};

/**
 * The graph explorer's **list** — every minted graph, grouped by name (the
 * primary object; a name is a lineage of immutable versions). Each group shows its
 * version count and the effective version's summary; expanding a group selects that
 * effective version (issue #152 — the detail opens on the first click) and reveals the
 * full lineage newest-first, each row carrying its `graph_id`, `created_at`, and an
 * **effective** / **superseded** / **retired** badge — the `graphs` row itself is
 * never mutated (still insert-only), the marker is the `effective`/`retired` facts
 * `GET /api/graphs` derives (issue #101's reversible lifecycle brake, layered on top
 * of the pre-#101 `effective` derivation). Any version, effective, superseded, or
 * retired, is selectable and opens identically (`selectGraph`); retiring/re-enabling
 * itself is driven from the detail view (`graph-detail.ts`), not this list.
 *
 * Built on `fleet-kit-select-row` at both levels, `run-list.ts`'s own shape: a
 * group's header row is itself a selectable row (selected when its effective
 * version is the one currently open — the id that header already displays and the
 * version a header click opens), and each lineage row underneath is a second,
 * stepped-down `fleet-kit-select-row`. Every row is two lines — a headline and a
 * meta line with a right-aligned trailing element (`margin-left: auto`,
 * `finding-list.css`'s own `.fl-ref` shape) — the same three-tier text scale
 * `run-list.css`'s own doc comment spells out, stepped down one tier for the
 * lineage rows since they sit a level below the group headline.
 *
 * Retired versions are **filtered out by default** ({@link showRetired}) — a
 * retired version is one deliberately taken out of name resolution (issue #101), so
 * carrying it in the default list makes every lineage read longer than the set an
 * operator can actually pin work to. The filter is a chip above the list rather than
 * a hidden preference, and it names how many versions it is holding back, so the
 * omission is never silent. Two things are never hidden: a group whose *only*
 * versions are retired still disappears entirely (there is nothing left to show),
 * but the currently selected version always survives the filter — deep-linking
 * straight to a retired graph's detail must still reveal its row, exactly as
 * {@link isExpanded} reveals its group.
 *
 * Filtering here, in the presentational list, and not in the query: the hub serves
 * the whole lineage on purpose (`GET /api/graphs` derives `retired` rather than
 * omitting the row), and the toggle has to be able to bring them straight back
 * without a refetch.
 *
 * Presentational only: `graphs`/`selectedGraphId` are plain inputs, no query
 * injection (`bzh:frontend-container-presentational`) — {@link GraphExplorer}
 * supplies both from `injectHubGraphsQuery`, keeping only the query and the
 * `KitAsyncStateValue` for itself.
 */
@Component({
  selector: 'fleet-graph-explorer-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge, KitChip, KitSelectRow],
  templateUrl: './graph-explorer-list.html',
  styleUrl: './graph-explorer-list.css',
})
export class GraphExplorerList {
  /** The resolved graph summaries to group and render. */
  readonly graphs = input.required<readonly GraphSummaryView[]>();

  /** The currently open detail's graph id, or `null` — highlights its row. */
  readonly selectedGraphId = input<string | null>(null);

  /** Emitted with the `graph_id` of a clicked row, effective or superseded alike — or
   * of a group's effective version when its header expands the group. */
  readonly selectGraph = output<string>();

  /** Whether retired versions are listed. Off by default — see the class doc. */
  protected readonly showRetired = signal(false);

  /** The summaries the list actually renders: every one of them when
   * {@link showRetired} is on, otherwise every non-retired one plus the current
   * selection, so a deep link into a retired version never lands on a list that
   * does not contain it. */
  private readonly visibleGraphs = computed<readonly GraphSummaryView[]>(() => {
    if (this.showRetired()) return this.graphs();
    const selected = this.selectedGraphId();
    return this.graphs().filter((g) => !g.retired || g.graph_id === selected);
  });

  /** How many versions the filter is currently holding back — the chip's own count,
   * and `0` whenever the filter is off. Counted against what is rendered rather than
   * against `retired` alone, so a retired version kept visible because it is selected
   * is not also reported as hidden. */
  protected readonly hiddenRetiredCount = computed<number>(() => this.graphs().length - this.visibleGraphs().length);

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
    const byName = new Map<string, GraphSummaryView[]>();
    for (const summary of this.visibleGraphs()) {
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

  /** Flip the retired filter. A group left with no visible versions simply stops
   * rendering, so nothing has to reconcile expansion state against the change. */
  protected toggleShowRetired(): void {
    this.showRetired.update((shown) => !shown);
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
  protected versionLabel(version: GraphSummaryView): VersionLabel {
    if (version.effective) return 'effective';
    if (version.retired) return 'retired';
    return 'superseded';
  }

  /** {@link LIFECYCLE_TONE}'s lookup, bound to a version rather than a bare label so
   * the template calls one method per row. */
  protected badgeTone(version: GraphSummaryView): Tone {
    return LIFECYCLE_TONE[this.versionLabel(version)];
  }
}
