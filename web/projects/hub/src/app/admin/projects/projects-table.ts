import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { compactRef, KitAsyncState, type KitAsyncStateValue, KitBadge, KitSelectRow, KitSwatch } from 'fleet';

import type { ProjectRowVm } from './projects.model';

/**
 * The tenant's projects as a selection list — each row a status light (green wired, red wanting, neutral while
 * unjudged) and the name over the badge naming what a wanting project needs and the project's short id.
 * Presentational: rows in, picks out by id.
 */
@Component({
  selector: 'app-projects-table',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitSelectRow, KitSwatch],
  templateUrl: './projects-table.html',
  styleUrl: './projects-table.css',
})
export class ProjectsTable {
  readonly rows = input.required<readonly ProjectRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();
  readonly selectedKey = input<string | null>(null);

  readonly pick = output<string>();

  protected lightLabel(row: ProjectRowVm): string {
    if (row.wired) return 'wired';
    return row.needs !== null ? 'missing wiring' : 'not yet judged';
  }

  /** The short form of the row's id, behind a method so the template calls it bound. */
  protected ref(row: ProjectRowVm): string {
    return compactRef(row.projectId);
  }
}
