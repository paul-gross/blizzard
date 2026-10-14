import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { KitBadge } from 'fleet';

import type { HostRunnerRow } from './host-runners.model';

/**
 * The host's runner rows — one per runner the host serves or holds halted: its name
 * (linking to its board when it serves), its brake, its agents in use, and one line per
 * binding; a halted runner shows its reasons instead.
 *
 * Presentational only (`bzh:frontend-container-presentational`): {@link HostRunners}
 * folds the host's read into the rows.
 */
@Component({
  selector: 'app-host-runners-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, RouterLink],
  templateUrl: './host-runners-view.html',
  styleUrl: './host-runners-view.css',
})
export class HostRunnersView {
  readonly runners = input.required<readonly HostRunnerRow[]>();
}
