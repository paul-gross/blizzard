import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { type RunnerCapability, harnessName, KitBadge, type Tone } from 'fleet';

/**
 * The runner registry's per-capability render — one distinct, labelled
 * badge per reported harness binding, so a multi-harness runner never collapses its
 * bindings into one undifferentiated row. A runner reporting none renders its own
 * settled empty branch rather than nothing at all — an empty list here means
 * "reported zero capabilities" (loading/error are gated upstream, {@link RunnerPanelView}).
 *
 * Presentational only.
 */
@Component({
  selector: 'app-capability-badge-group',
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
