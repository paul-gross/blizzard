import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitBadge, KitSelectRow } from 'fleet';

import type { ProjectRowVm } from './projects.model';

/**
 * The tenant's projects as a selection list — each row the project's name and revision over how
 * it is wired (its linked sources, its linked repositories, its queue), with a dot on a wired
 * project and a badge naming what a wanting one needs. Presentational: rows in, picks out by id.
 */
@Component({
  selector: 'app-projects-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitSelectRow],
  templateUrl: './projects-table.html',
  styleUrl: './projects-table.css',
})
export class ProjectsTable {
  readonly rows = input.required<readonly ProjectRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly selectedKey = input<string | null>(null);

  readonly pick = output<string>();
}
