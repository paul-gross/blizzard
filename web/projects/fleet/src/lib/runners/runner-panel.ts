import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import type { ChunkStatus, RunnerView } from '../api/hub';
import { hasPermission, injectMeQuery } from '../auth/me.query';
import { compactRef } from '../compact-ref';
import { injectHubChunksQuery } from '../chunks/chunks.query';
import type { KitAsyncStateValue } from '../kit/kit-async-state';
import { asyncState } from '../query-state';
import { RunnerPanelView } from './runner-view';
import { injectHubRunnersQuery } from './runners.query';
import { injectRunnerPauseMutation } from './runners.mutations';

/** One claim line under a registry row: the chunk a runner holds, where it sits, and
 * how it is doing there (issue #156) — the node alone reads the same for a chunk
 * actively running and one parked `needs_human`. */
export interface ClaimLine {
  readonly chunkId: string;
  readonly shortId: string;
  readonly node: string;
  readonly status: ChunkStatus;
}

/** A registry row: the runner plus the claims it holds, pre-folded so the
 * presentational sibling needs no second read to render them. `used` is the slot
 * bar's numerator — environments held by this runner's live routes (issue #69). */
export interface RunnerRow extends RunnerView {
  readonly claims: readonly ClaimLine[];
  readonly used: number;
}

/**
 * The runner panel — the fleet registry in the board's right rail: each
 * registered runner with its derived **liveness** (`online` vs the
 * staleness threshold), last-seen time, and **paused** state, plus a pause/resume
 * toggle — the operator's brake, declarative state the runner reads on its
 * outbound pull.
 *
 * A container (issue #80): it owns the registry + chunks queries and the pause
 * mutation, through the generated client (bzh:generated-client), folds each
 * runner's claims off the chunk list, and renders the presentational
 * {@link RunnerPanelView}. The live-update service re-reads on `runner-changed`.
 */
@Component({
  selector: 'fleet-runner-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RunnerPanelView],
  template: `<fleet-runner-view [rows]="rows()" [state]="state()" [canPause]="canPause()" (togglePause)="toggle($event)" />`,
})
export class RunnerPanel {
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly pauseMutation = injectRunnerPauseMutation();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may operate the hub pause/resume brake
   * (`runner:pause`, admin-tier — issue #93). Passed to the presentational view, which
   * withholds the toggle button when false so a `contributor` never sees a control that
   * would 403. `null`/pending resolves to `false` (hidden until confirmed). */
  protected readonly canPause = computed(() => hasPermission(this.meQuery.data(), 'runner:pause'));

  /** The fleet registry; empty until the first read resolves. */
  private readonly runners = computed<readonly RunnerView[]>(() => this.runnersQuery.data() ?? []);

  /** The registry's async state (AC 3) — derived from the runners query alone;
   * the chunks query only folds claims onto rows already known to exist. */
  protected readonly state = computed<KitAsyncStateValue>(() =>
    asyncState(this.runnersQuery, this.runners().length === 0),
  );

  /** Every routed chunk grouped by the runner holding it — each as a claim line
   * (short name + current node + status) for the registry rows.
   *
   * No status filter by design: `ChunkSummary.runner_id` is already in-progress-only
   * (issue #140 — see its own docs), so `runner_id` set *is* "currently holds a route on". */
  private readonly claims = computed<Map<string, ClaimLine[]>>(() => {
    const grouped = new Map<string, ClaimLine[]>();
    for (const chunk of this.chunksQuery.data() ?? []) {
      if (!chunk.runner_id) continue;
      const lines = grouped.get(chunk.runner_id) ?? [];
      lines.push({
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
        status: chunk.status,
      });
      grouped.set(chunk.runner_id, lines);
    }
    return grouped;
  });

  /** Environments held per runner — the slot bar's numerator (issue #69), summed from
   * each of its chunks' `environment_count`. A grouped chunk holding >1 environment
   * counts them all, so a runner working one 3-env chunk reads as using 3 slots, not 1.
   * Environments are exclusively leased and a runner's chunks are distinct, so a plain
   * sum needs no dedup. Unfiltered by status for the same reason {@link claims} is. */
  private readonly usedByRunner = computed<Map<string, number>>(() => {
    const used = new Map<string, number>();
    for (const chunk of this.chunksQuery.data() ?? []) {
      if (!chunk.runner_id) continue;
      used.set(chunk.runner_id, (used.get(chunk.runner_id) ?? 0) + (chunk.environment_count ?? 0));
    }
    return used;
  });

  /** Each runner with its claims and slot-bar numerator folded on, for the view. */
  protected readonly rows = computed<readonly RunnerRow[]>(() =>
    this.runners().map((runner) => ({
      ...runner,
      claims: this.claims().get(runner.runner_id) ?? [],
      used: this.usedByRunner().get(runner.runner_id) ?? 0,
    })),
  );

  protected toggle(runner: RunnerView): void {
    this.pauseMutation.mutate({ runnerId: runner.runner_id, paused: !runner.hub_paused });
  }
}
