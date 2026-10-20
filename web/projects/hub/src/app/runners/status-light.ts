import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitBeacon } from 'fleet';
import type { ConnectionLight } from './connection-status.model';

/**
 * A host's or runner's status light — its lit beacon, or the hollow mark of one never connected.
 * Presentational and input-only: the model decides the light, and the host element's box sets the
 * size the hollow mark fills.
 */
@Component({
  selector: 'app-status-light',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBeacon],
  templateUrl: './status-light.html',
  styleUrl: './status-light.css',
})
export class StatusLight {
  readonly light = input.required<ConnectionLight>();
}
