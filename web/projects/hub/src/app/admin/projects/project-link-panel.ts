import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitBadge, KitButton, KitErrorLine } from 'fleet';

import type { ProjectLinkRowVm } from './projects.model';

/**
 * One kind of a project's links — the work sources it draws from, or the repositories it may
 * land in: what linking means, one row per link in force with its Unlink, and Link. The
 * built-in source is linked to every project and offers no Unlink. Presentational.
 */
@Component({
  selector: 'app-project-link-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, KitButton, KitErrorLine],
  templateUrl: './project-link-panel.html',
  styleUrl: './project-link-panel.css',
})
export class ProjectLinkPanel {
  readonly heading = input.required<string>();
  readonly explainer = input.required<string>();
  readonly rows = input.required<readonly ProjectLinkRowVm[]>();
  readonly emptyText = input.required<string>();
  readonly linkLabel = input.required<string>();
  /** Whether the viewer may link and unlink here. */
  readonly canEdit = input(false);
  /** The key of the link a write is in flight for, whose Unlink waits. */
  readonly pendingKey = input<string | null>(null);
  readonly error = input<string | null>(null);
  readonly testid = input.required<string>();

  readonly link = output<void>();
  readonly unlink = output<ProjectLinkRowVm>();
}
