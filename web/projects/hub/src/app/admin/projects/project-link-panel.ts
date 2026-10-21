import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitBadge, KitButton, KitErrorLine, KitPanel, KitPanelHeader } from 'fleet';

import type { ProjectLinkRowVm } from './projects.model';

/**
 * One kind of a project's links — the work sources it draws from, or the repositories it may
 * land in: what linking means, one row per link in force with its Unlink, and Link. The
 * built-in source is linked to every project and offers no Unlink. Presentational.
 */
@Component({
  selector: 'app-project-link-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitButton, KitErrorLine, KitPanel, KitPanelHeader],
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
  /** What the rows rest on: they render once it is ready, and the empty copy only once the reads settled empty. */
  readonly state = input<KitAsyncStateValue>('ready');
  /** The keys of the links an unlink is in flight for, whose Unlink waits. */
  readonly pendingKeys = input<ReadonlySet<string>>(new Set());
  readonly error = input<string | null>(null);
  readonly testid = input.required<string>();

  readonly link = output<void>();
  readonly unlink = output<ProjectLinkRowVm>();

  /** The count waits for the reads, so a still-loading panel does not claim none. */
  protected readonly countShown = computed(() => this.state() === 'ready' || this.state() === 'empty');
}
