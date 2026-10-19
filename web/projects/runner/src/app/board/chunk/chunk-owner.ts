import { Injectable, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { type ChunkStepSession, type DaemonTarget, type KitAsyncStateValue, runnerClient } from 'fleet';

import { injectPanelLeases } from '../../core/panel-reads';
import type { RunnerOwner } from '../../core/panel-reads.model';
import { stepSessions } from '../../core/worker-session';

/**
 * The runner that owns the routed chunk — the runner whose leases name it, its newest
 * attempt's, looked for across every serving runner whatever the filters say, so a link to
 * a chunk opens however the panel is narrowed. Provided on {@link RunnerChunkPage}, so it
 * lives for one visit.
 */
@Injectable()
export class ChunkOwner {
  private readonly route = inject(ActivatedRoute);
  private readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });
  private readonly leases = injectPanelLeases({ scope: 'all', byProject: false });

  /** The routed chunk's id. */
  readonly chunkId = computed(() => this.params().get('chunkId'));

  /** The chunk's runner, `null` until the leases name it. */
  readonly owner = computed<RunnerOwner | null>(() => {
    const chunkId = this.chunkId();
    return this.leases.data().find((lease) => lease.chunk_id === chunkId)?.owner ?? null;
  });

  /** The owning runner's sessions for the chunk, keyed by `(node_id, epoch)` — the Node
   * history rows' enrichment (`CHUNK_STEP_SESSIONS`). */
  readonly stepSessions = computed<ReadonlyMap<string, ChunkStepSession>>(() => {
    const chunkId = this.chunkId();
    const owner = this.owner();
    if (owner === null) return new Map();
    return stepSessions(this.leases.data().filter((lease) => lease.chunk_id === chunkId && lease.owner.runnerId === owner.runnerId));
  });

  /** The chunk page's daemon target — the runner client under the chunk's runner — or
   * `null`, holding every read, while no runner owns it. */
  readonly target = computed<DaemonTarget | null>(() => {
    const owner = this.owner();
    return owner === null ? null : { plane: 'runner', client: runnerClient, runnerId: owner.runnerId };
  });

  /** Loading while the runners' leases are, ready once a runner owns the chunk, empty when none does. */
  readonly state = computed<KitAsyncStateValue>(() => {
    if (this.owner() !== null) return 'ready';
    if (this.leases.isPending()) return 'loading';
    return this.leases.isError() ? 'error' : 'empty';
  });
}
