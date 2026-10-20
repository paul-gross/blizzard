import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { type BeaconTone, FleetWhen, KitBadge, KitBeacon } from 'fleet';

import type { HostRegistrationSummary } from './host-registration.model';

/**
 * The host page's title bar: the host's light, its name at its hub, its short ref, and its
 * registration — the hub's answer and how long ago it gave it. The light and the answer wait
 * on a registration to report; the name and ref stand without one.
 *
 * Presentational only: the page folds the registration and hands each piece down.
 */
@Component({
  selector: 'app-host-title',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitBadge, KitBeacon],
  templateUrl: './host-title.html',
  styleUrl: './host-title.css',
})
export class HostTitle {
  readonly name = input.required<string>();
  /** The host's id at its hub, `null` before it registers; titles the ref. */
  readonly hostId = input.required<string | null>();
  readonly hostRef = input.required<string | null>();
  readonly registration = input.required<HostRegistrationSummary | null>();
  readonly tone = input.required<BeaconTone>();
}
