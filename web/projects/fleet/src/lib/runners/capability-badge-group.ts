import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import type { RunnerCapability } from '../api/hub';
import { harnessName } from '../harness-name';
import { KitBadge } from '../kit/kit-badge';
import type { Tone } from '../kit/tone';

/**
 * The runner registry's per-capability render (blizzard#441) — one distinct, labelled
 * badge per reported harness binding, so a multi-harness runner never collapses its
 * bindings into one undifferentiated row. A runner reporting none renders its own
 * settled empty branch rather than nothing at all — {@link RunnerPanelView}'s async
 * `state` already gates the loading/error cases, so an empty list here is always a
 * resolved "reported zero capabilities", never a spinner in disguise.
 *
 * Presentational only.
 */
@Component({
  selector: 'fleet-capability-badge-group',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge],
  templateUrl: './capability-badge-group.html',
  styleUrl: './capability-badge-group.css',
})
export class CapabilityBadgeGroup {
  /** Every capability this runner reported, in registration order. */
  readonly capabilities = input.required<readonly RunnerCapability[]>();
  protected readonly harnessName = harnessName;

  /** `done` (green) for an available binding, `needs` (red) for one whose own health
   * check failed — the same tone ladder the claim-status badges already use, so
   * availability reads with the board's existing green/red vocabulary rather than a
   * capability-local one. */
  protected toneFor(capability: RunnerCapability): Tone {
    return capability.available ? 'done' : 'needs';
  }

  /** Names harness and availability for the badge's accessible label. */
  protected ariaLabel(capability: RunnerCapability): string {
    const availability = capability.available ? 'available' : 'unavailable';
    return `${harnessName(capability.harness_id)}, ${availability}`;
  }
}
