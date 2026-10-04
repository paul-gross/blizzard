import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { restingAsyncState, type KitAsyncStateValue } from 'fleet';
import { FleetRunDelta, type RunDeltaVm } from './run-delta';
import { injectHubRunDeltaQuery } from './garden-runs.query';
import { map } from 'rxjs';

import { GardeningRunsState } from './gardening-runs-state';
import { runDeltaVm } from './gardening-run-detail.model';

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
  styleUrl: '../core/gardening-detail-host.css',
})
export class GardeningRunDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly runs = inject(GardeningRunsState);

  /** The `chunkId` route param, or `null` on the bare child route. */
  private readonly chunkId = toSignal(this.route.paramMap.pipe(map((params) => params.get('chunkId'))), {
    initialValue: null,
  });

  private readonly deltaQuery = injectHubRunDeltaQuery(() => this.chunkId());

  protected readonly deltaVm = computed<RunDeltaVm | null>(() =>
    runDeltaVm(this.deltaQuery.data(), (chunkId) => this.runs.mintedAtFor(chunkId)),
  );

  /** `chunkId() === null` rests as `'empty'` *before* `asyncState` is consulted — `deltaQuery` is
   * `enabled: false` then, which reports `isPending()` forever, so "no run
   * selected" resolves to `'empty'` directly rather than a permanent spinner. */
  protected readonly deltaState = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.chunkId() === null, this.deltaQuery, false),
  );
}
