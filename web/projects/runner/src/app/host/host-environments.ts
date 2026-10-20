import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { KitAsyncState, type KitAsyncStateValue, KitBeacon, KitPanel, KitSwatch } from 'fleet';

import type { HostEnvGroup } from './host-environments.model';

/**
 * The host page's Workspaces · environments section: each workspace with how many of its
 * environments are in use, then its environments — a held one naming its chunk (opening on
 * the board), project, and runner, dimmed when a runner the filters leave out holds it; a
 * free one saying so.
 *
 * Presentational only: the page folds {@link HostEnvGroup}s and the section's async state.
 */
@Component({
  selector: 'app-host-environments',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBeacon, KitPanel, KitSwatch, RouterLink],
  templateUrl: './host-environments.html',
  styleUrl: './host-environments.css',
})
export class HostEnvironmentsView {
  readonly groups = input.required<readonly HostEnvGroup[]>();
  /** "9 / 18 in use" across every workspace, `null` while none is reported. */
  readonly inUseLabel = input.required<string | null>();
  readonly state = input.required<KitAsyncStateValue>();
}
