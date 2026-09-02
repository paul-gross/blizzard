import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import {
  asyncState,
  defaultRoutineWindow,
  FleetRunDelta,
  FleetRunList,
  injectHubRunDeltaQuery,
  injectHubRunsQuery,
  type KitAsyncStateValue,
  type RunDeltaVm,
  type RunListDeliveredSetVm,
  type RunListRowVm,
} from 'fleet';
import { map } from 'rxjs';

/** One `revisions` map, rendered the same `repo@revision, …` label
 * `gardening-routines-page.ts`'s own `lastSwept` reduction uses — sorted so the label
 * is deterministic across repeated reads of the same set. */
function revisionsLabel(revisions: Record<string, string>): string {
  return (
    Object.entries(revisions)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([repo, rev]) => `${repo}@${rev}`)
      .join(', ') || '—'
  );
}

/**
 * The `/gardening/runs-and-findings` sub-tab (blizzard#401 Phase 3,
 * `plans/garden/user-interface.md`'s "Reading what a run saw" section) — the run
 * list, and the selected run's own delta. `graphs-page.ts`'s own list-stays-mounted
 * shape: both `runs-and-findings` and `runs-and-findings/:chunkId` render this one
 * component (`app.routes.ts`), and the optional `chunkId` route param — read off
 * `paramMap`, not an `@Input` — drives which run's delta shows, so picking a run is a
 * deep-linkable, refresh-safe drill-in rather than a route swap that would drop the
 * list.
 *
 * A container: it injects the run list and (route-param-gated) run delta queries and
 * forwards plain view models to the presentational {@link FleetRunList} and
 * {@link FleetRunDelta} — neither of which injects a query of its own.
 */
@Component({
  selector: 'app-gardening-runs-findings-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRunList, FleetRunDelta],
  templateUrl: './gardening-runs-findings-page.html',
  styleUrl: './gardening-runs-findings-page.css',
})
export class GardeningRunsFindingsPage {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  /** The list's fixed reporting window — `gardening-routines-page.ts`'s own
   * `window`, computed once at construction rather than re-derived per render; a page
   * reload is what refreshes it. Shares the routine trend/sweeps vocabulary (D5)
   * rather than the read's own 24-hour server default, so an operator sees the same
   * "last 28 days" a routine's own trend already reports. */
  private readonly window = defaultRoutineWindow(Date.now());

  private readonly runsQuery = injectHubRunsQuery(() => this.window.since);

  /** The `chunkId` route param, or `null` on the bare `runs-and-findings` list route —
   * `graphs-page.ts`'s own `graphId` read. */
  protected readonly chunkId = toSignal(this.route.paramMap.pipe(map((params) => params.get('chunkId'))), {
    initialValue: null,
  });

  private readonly deltaQuery = injectHubRunDeltaQuery(() => this.chunkId());

  protected readonly listRows = computed<readonly RunListRowVm[]>(() =>
    (this.runsQuery.data() ?? []).map((row) => ({
      chunkId: row.chunk_id,
      routineName: row.routine_name,
      scopeSlug: row.scope_slug,
      mode: row.mode,
      mintedAt: row.minted_at,
      outcome: row.outcome,
      escalated: row.escalation !== null,
      delivered: row.delivered.map(
        (d): RunListDeliveredSetVm => ({
          findingSetId: d.finding_set_id,
          revisionsLabel: revisionsLabel(d.revisions),
          measurement: d.measurement,
        }),
      ),
    })),
  );

  protected readonly listState = computed<KitAsyncStateValue>(() =>
    asyncState(this.runsQuery, this.listRows().length === 0),
  );

  protected readonly deltaVm = computed<RunDeltaVm | null>(() => {
    const delta = this.deltaQuery.data();
    if (delta === undefined) return null;
    return {
      chunkId: delta.chunk_id,
      routineName: delta.routine_name,
      scopeSlug: delta.scope_slug,
      mode: delta.mode,
      outcome: delta.outcome,
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
        observed: set.observed,
        gone: set.gone.map((g) => ({ findingId: g.finding_id, note: g.note })),
      })),
    };
  });

  /** `chunkId() === null` branches out *before* `asyncState` — `deltaQuery` is
   * `enabled: false` then, which reports `isPending()` forever
   * (`query-state.ts`'s own documented trap), so "nothing selected" must resolve to
   * `'empty'` directly rather than fall into a permanent loading spinner. Once a run
   * is selected, `isEmpty` is always `false` (D4: a run with zero delivered sets
   * still renders as a normal, fully-read row, not an empty state) — only loading and
   * error need distinguishing there. */
  protected readonly deltaState = computed<KitAsyncStateValue>(() =>
    this.chunkId() === null ? 'empty' : asyncState(this.deltaQuery, false),
  );

  protected select(chunkId: string): void {
    void this.router.navigate(['/gardening', 'runs-and-findings', chunkId]);
  }
}
