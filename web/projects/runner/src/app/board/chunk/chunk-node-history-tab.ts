import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { type ArtifactView, ChunkArtifactBody, ChunkTimelineSelection, type hubApi } from 'fleet';

/**
 * The runner chunk detail page's Node history tab — the shared
 * {@link ChunkTimelineSelection}'s three-line rows beside the selected step's own
 * artifacts. Mirrors the hub's own `chunk-node-history-tab.ts`, minus its transcript
 * half: the hub's per-step transcript drill-down reads through hub-only routes
 * (`reject_runner_principal`), so this tab stops at artifacts, with no
 * {@link KitAccordionSection} around them either — one section, always visible,
 * nothing to collapse against.
 *
 * `graphLinkBase` is left at {@link ChunkTimelineSelection}'s own `null` default — the
 * runner has no `/graphs` route to point a multi-graph row's badge at
 * (`ChunkFacts.graphLinkBase`'s own doc comment states the same reason).
 *
 * Presentational (`bzh:frontend-container-presentational`): {@link ChunkDetailPage} owns
 * the `?step=` selection and the per-step artifact filter, forwarding both down —
 * this component injects nothing.
 */
@Component({
  selector: 'app-chunk-node-history-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, ChunkTimelineSelection],
  templateUrl: './chunk-node-history-tab.html',
  styleUrl: './chunk-node-history-tab.css',
})
export class ChunkNodeHistoryTab {
  readonly detail = input.required<hubApi.ChunkDetail>();

  /** The raw `?step` URL param — forwarded straight to {@link ChunkTimelineSelection}
   * with no lookup against the timeline's own rows here. */
  readonly selectedKey = input<string | null>(null);

  /** The selected step's own artifacts, already filtered by the container (exact
   * `(node_id, epoch)`, never latest-by-node). */
  readonly stepArtifacts = input<readonly ArtifactView[]>([]);

  /** Forwarded straight from {@link ChunkTimeline.pickStep} — a row's join key when the
   * operator activates it, or `null` when they clear the selection by re-activating the
   * already-selected row. */
  readonly pickStep = output<string | null>();
}
