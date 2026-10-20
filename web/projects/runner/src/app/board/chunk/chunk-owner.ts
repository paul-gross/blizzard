import { Injectable, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { type ChunkStepSession, type DaemonTarget, type KitAsyncStateValue, runnerClient } from 'fleet';

import { injectPanelLeases, injectPanelRunners } from '../../core/panel-reads';
import type { RunnerOwner } from '../../core/panel-reads.model';
import { stepSessions } from '../../core/worker-session';
import { chunkReadOwner } from './chunk-owner.model';

/**
 * The runner that owns the routed chunk — the runner whose leases name it, its newest
 * attempt's, looked for across every serving runner whatever the filters say, so a link to
 * a chunk opens however the panel is narrowed; a chunk no recent lease names reads under a
 * serving runner, every one proxying a chunk's detail from the hub. Provided on {@link RunnerChunkPage}, so it
 * lives for one visit.
 */
@Injectable()
export class ChunkOwner {
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });
  private readonly leases = injectPanelLeases({ scope: 'all', byProject: false });
  private readonly runners = injectPanelRunners();

  /** The routed chunk's id. */
  readonly chunkId = computed(() => this.params().get('chunkId'));

  /** The runner the chunk is read under, `null` while the host serves none. */
  readonly owner = computed<RunnerOwner | null>(() =>
    chunkReadOwner(this.leases.data(), this.chunkId(), this.runners.all(), this.runners.scoped()),
  );

  /** The chunk's sessions across every runner that ran it, keyed by `(node_id, epoch)` — the Node history rows'
   * enrichment (`CHUNK_STEP_SESSIONS`). A chunk that moved runners keeps its earlier steps' sessions on the runner
   * that ran them, so they are not narrowed to its current owner. */
  readonly stepSessions = computed<ReadonlyMap<string, ChunkStepSession>>(() => {
    const chunkId = this.chunkId();
    return stepSessions(this.leases.data().filter((lease) => lease.chunk_id === chunkId));
  });

  /** The chunk page's daemon target — the runner client under the chunk's runner — or
   * `null`, holding every read, while no runner serves it. */
  readonly target = computed<DaemonTarget | null>(() => {
    const owner = this.owner();
    return owner === null ? null : { plane: 'runner', client: runnerClient, runnerId: owner.runnerId };
  });

  /** Loading while the runners or their leases are, ready once the leases settle on a runner to read under,
   * empty when the host serves none — a failed lease read still falls back. Waiting on the leases keeps a leased chunk from first reading under a
   * fallback runner. */
  readonly state = computed<KitAsyncStateValue>(() => {
    if (this.runners.pending() || this.leases.isPending()) return 'loading';
    return this.owner() === null ? 'empty' : 'ready';
  });
}
