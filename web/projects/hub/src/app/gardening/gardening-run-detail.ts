import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { asyncState, FleetRunDelta, injectHubRunDeltaQuery, type KitAsyncStateValue, type RunDeltaVm } from 'fleet';
import { map } from 'rxjs';

import { GardeningRunsState } from './gardening-runs-state';

/** One `revisions` map, rendered `repo@revision, …` — `gardening-routine-detail.ts`'s
 * own `lastSwept` reduction, sorted for a deterministic label. */
function revisionsLabel(revisions: Record<string, string>): string {
  return (
    Object.entries(revisions)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([repo, rev]) => `${repo}@${rev}`)
      .join(', ') || '—'
  );
}

/**
 * The selected run's own delta — the right-hand child of `/gardening/runs`
 * (`gardening-runs-page.ts` owns the list beside it). Mounted by both of that
 * route's children, so the bare one renders the pane's own "nothing selected"
 * empty state.
 *
 * A container: it injects the delta read and forwards a plain view model to the
 * presentational {@link FleetRunDelta}, which injects no query of its own. The
 * run's `minted_at` is not on the delta read at all — it comes off the matching
 * row of the list beside this pane, through the {@link GardeningRunsState} the two
 * share.
 */
@Component({
  selector: 'app-gardening-run-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRunDelta],
  templateUrl: './gardening-run-detail.html',
  styleUrl: './gardening-detail-host.css',
})
export class GardeningRunDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly runs = inject(GardeningRunsState);

  /** The `chunkId` route param, or `null` on the bare child route. */
  private readonly chunkId = toSignal(this.route.paramMap.pipe(map((params) => params.get('chunkId'))), {
    initialValue: null,
  });

  private readonly deltaQuery = injectHubRunDeltaQuery(() => this.chunkId());

  protected readonly deltaVm = computed<RunDeltaVm | null>(() => {
    const delta = this.deltaQuery.data();
    if (delta === undefined) return null;
    return {
      chunkId: delta.chunk_id,
      routineName: delta.routine_name,
      scopeSlug: delta.scope_slug,
      mintedAt: this.runs.mintedAtFor(delta.chunk_id),
      escalation:
        delta.escalation === null
          ? null
          : {
              nodeName: delta.escalation.node_name,
              takeoverCommand: delta.escalation.takeover_command,
              wrappedTakeoverCommand: delta.escalation.wrapped_takeover_command,
            },
      sets: delta.sets.map((set) => ({
        findingSetId: set.finding_set_id,
        revisionsLabel: revisionsLabel(set.revisions),
        measurement: set.measurement,
        added: set.added.map((a) => ({
          findingId: a.finding_id,
          findingClass: a.class,
          locus: a.locus,
          summary: a.summary,
          introduced: a.introduced,
        })),
        observed: set.observed.map((o) => ({
          findingId: o.finding_id,
          findingClass: o.class,
          locus: o.locus,
          summary: o.summary,
        })),
        gone: set.gone.map((g) => ({ findingId: g.finding_id, note: g.note })),
      })),
    };
  });

  /** `chunkId() === null` branches out *before* `asyncState` — `deltaQuery` is
   * `enabled: false` then, which reports `isPending()` forever, so "no run
   * selected" resolves to `'empty'` directly rather than a permanent spinner. */
  protected readonly deltaState = computed<KitAsyncStateValue>(() =>
    this.chunkId() === null ? 'empty' : asyncState(this.deltaQuery, false),
  );
}
