import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { ChunkStatus } from '../api/hub';
import { STATUS_TONE } from '../chunk-lanes';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';
import { KitPaceBar } from '../kit/kit-pace-bar';
import { KitPanel } from '../kit/kit-panel';
import { KitSlotBar } from '../kit/kit-slot-bar';
import type { Tone } from '../kit/tone';
import { formatSeenAgo } from '../when';
import type { RunnerRow } from './runner-panel';

/**
 * The runner registry's presentational half (issue #80) — the registry
 * table's markup, liveness dot, claim lines, pause-brake badges, and the
 * pause/resume toggle. Renders exactly the rows it is handed; injects no
 * query or mutation, so a spec drives it with plain inputs.
 */
@Component({
  selector: 'fleet-runner-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitPaceBar, KitPanel, KitSlotBar],
  templateUrl: './runner-view.html',
  styleUrl: './runner-view.css',
})
export class RunnerPanelView {
  /** The registry rows to render — each runner plus its pre-folded claims. */
  readonly rows = input.required<readonly RunnerRow[]>();

  /** The registry query's async state (AC 3) — loading/error withhold the empty
   * copy until the read resolves. */
  readonly state = input.required<KitAsyncStateValue>();

  /** Whether to render the hub pause/resume brake (issue #93) — the container passes
   * `hasPermission(me, 'runner:pause')`. Admin-tier: a `contributor` sees the registry
   * and its liveness/paused badges but not the toggle it could only 403 on. Defaults
   * `false` so the brake stays withheld until permission is confirmed (no flash of a
   * control the identity cannot use). Under `auth.mode = "none"` the implicit operator
   * holds every permission, so the brake renders exactly as before. */
  readonly canPause = input(false);

  /** A claim's badge tone, read straight off `chunk-lanes.ts`'s `STATUS_TONE` — the
   * single owner of the status→tone fold the board card colors from too (issue #156).
   * No local table: a claim's color and its card's can never drift apart. */
  protected toneFor(status: ChunkStatus): Tone {
    return STATUS_TONE[status];
  }

  /** Whether to render the env-slot bar (issue #69): only when the runner reported a
   * capacity. A runner registered by a client that predates the field has a null (or
   * absent) `env_capacity` and gets no bar, rather than a misleading zero-slot one. */
  protected hasCapacity(row: RunnerRow): boolean {
    return row.env_capacity !== null && row.env_capacity !== undefined;
  }

  /** Emitted with the row to flip the **hub** brake on — the container reads
   * `hub_paused` off it to decide pause vs. resume. Named `togglePause`, not
   * `toggle` — `@angular-eslint/no-output-native` forbids an output shadowing
   * the native DOM `toggle` event. */
  readonly togglePause = output<RunnerRow>();

  /**
   * Why the runner stopped itself (issue #61): a spend-ceiling crossing names the ceiling
   * and the spend it reported (`locally_paused_reason`); a manual `blizzard runner pause`
   * carries none, so this falls back to the generic clear-it-yourself hint.
   */
  protected localPauseHint(row: RunnerRow): string {
    return row.locally_paused_reason ?? 'This runner paused itself. Clear it on the runner: blizzard runner start';
  }

  /** Why resuming at the hub may not start a runner: its own brake is not ours to clear. */
  protected toggleHint(row: RunnerRow): string {
    if (row.hub_paused && row.locally_paused) {
      return 'Resuming here clears the hub brake only — this runner also paused itself.';
    }
    return row.hub_paused ? 'Resume this runner at the hub' : 'Pause this runner at the hub';
  }

  /**
   * A compact "seen 12s ago" liveness label from `last_seen_at` (`bzh:utc-instants`).
   *
   * Liveness is decided where both instants share one clock — the hub, via `online`
   * (`derive_online` compares `last_seen_at` against the hub's own clock); this
   * label is decoration computed against the *browser's* clock, so it defers to the
   * shared skew-tolerant `formatSeenAgo` (`when.ts`) rather than re-deriving its own
   * tolerance window.
   */
  protected seenLabel(row: RunnerRow): string {
    return formatSeenAgo(row.last_seen_at, row.online);
  }
}
