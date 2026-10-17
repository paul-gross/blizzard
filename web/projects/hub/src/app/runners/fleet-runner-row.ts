import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type ChunkStatus, KitBadge, KitButton, RunnerConnection, STATUS_TONE, type Tone, KitBeacon } from 'fleet';
import { CapabilityBadgeGroup } from './capability-badge-group';
import { GateBadgeGroup } from './gate-badge-group';
import { localPauseHint, runnerSeenLabel, runnerToggleHint, type RunnerRow } from './runner-rows';
import { ServedProjectGroup } from './served-project-group';

/**
 * One runner as a wide row on the Fleet tab's detail pane: its liveness and name, what it
 * binds and spends, its harnesses and gates, its state badges, the chunks it holds, and the
 * hub's Pause/Resume brake behind `runner:pause`.
 *
 * Presentational only: the container owns the permission, the pending set and the mutation.
 */
@Component({
  selector: 'app-fleet-runner-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBeacon, CapabilityBadgeGroup, GateBadgeGroup, KitBadge, KitButton, ServedProjectGroup],
  templateUrl: './fleet-runner-row.html',
  styleUrl: './fleet-runner-row.css',
})
export class FleetRunnerRow {
  readonly row = input.required<RunnerRow>();

  /** Whether the runner sits under a host — switching its served line to bindings mode. */
  readonly hosted = input(false);

  /** Whether to render the hub brake — the identity's `runner:pause`. */
  readonly canPause = input(false);

  /** Whether this runner's pause/resume is in flight. */
  readonly pausePending = input(false);

  /** The row to flip the hub brake on; the container reads `hub_paused` off it. */
  readonly togglePause = output<RunnerRow>();

  protected readonly connection = RunnerConnection;
  protected readonly seenLabel = runnerSeenLabel;
  protected readonly localPauseHint = localPauseHint;
  protected readonly toggleHint = runnerToggleHint;

  protected toneFor(status: ChunkStatus): Tone {
    return STATUS_TONE[status];
  }
}
