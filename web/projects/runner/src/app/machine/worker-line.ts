import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { KitBadge, type runnerApi } from 'fleet';

import { deriveLeaseStatus } from '../core/lease-status';
import { HeartbeatFreshness } from './heartbeat-freshness';

/**
 * A chunk's live worker on one line — the node it runs, the environment it holds, a
 * {@link HeartbeatFreshness} bar with the heartbeat's age, and its running/parked state as a
 * kit badge off the kernel's lease fold ({@link deriveLeaseStatus}). Presentational: the
 * chunk rows, the cards and the dock all render a chunk's one live lease through it.
 */
@Component({
  selector: 'app-worker-line',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HeartbeatFreshness, KitBadge],
  templateUrl: './worker-line.html',
  styleUrl: './worker-line.css',
})
export class WorkerLine {
  /** The live lease this line renders. */
  readonly lease = input.required<runnerApi.LeaseView>();

  /** Whether to lead with the node — off where the surrounding line already names it. */
  readonly showNode = input(true);

  protected readonly status = computed(() => deriveLeaseStatus(this.lease()));
  protected readonly isStale = computed(() => this.lease().state === 'stale');
}
