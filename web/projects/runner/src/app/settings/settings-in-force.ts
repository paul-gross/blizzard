import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { FleetWhen, KitBadge } from 'fleet';

import type { InForceLineVm } from './settings.model';

/** A settings record's `in_force` line: the revision its daemon runs and since when, a written revision still
 * waiting for the next tick, and a revision whose rebuild failed, with its error, while the older one stays in force. */
@Component({
  selector: 'app-settings-in-force',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge],
  templateUrl: './settings-in-force.html',
  styleUrl: './settings-in-force.css',
})
export class SettingsInForce {
  readonly line = input.required<InForceLineVm>();
  /** The prefix of the line's test ids: `<testid>-in-force`, `<testid>-in-force-failed`. */
  readonly testid = input.required<string>();
}
