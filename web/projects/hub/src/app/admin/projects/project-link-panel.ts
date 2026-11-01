import { CdkDrag, type CdkDragDrop, CdkDropList } from '@angular/cdk/drag-drop';
import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  KitAsyncState,
  type KitAsyncStateValue,
  KitBadge,
  KitButton,
  KitErrorLine,
  KitPanel,
  KitPanelHeader,
  type LandingPositionRequest,
} from 'fleet';

import { type LandingSection, type LandingSections, resolveLandingDrop } from './project-landing-order.model';
import type { ProjectLinkRowVm } from './projects.model';

/**
 * One kind of a project's links — the work sources it draws from, or the repositories it may
 * land in: what linking means, one row per link in force with its Unlink, and Link. The
 * built-in source is linked to every project and offers no Unlink. Given `sections`, the rows render as a landing
 * order — ranked, then "Unranked" — two connected drop lists armed only for `canEdit`, each drop resolved to the one
 * position it asks for. Presentational.
 */
@Component({
  selector: 'app-project-link-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkDrag, CdkDropList, KitAsyncState, KitBadge, KitButton, KitErrorLine, KitPanel, KitPanelHeader, NgTemplateOutlet],
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
  /** The rows as a landing order, split ranked and unranked; `null` lists them flat. */
  readonly sections = input<LandingSections | null>(null);

  readonly link = output<void>();
  readonly unlink = output<ProjectLinkRowVm>();
  /** The one landing position a drop asks for. */
  readonly reposition = output<LandingPositionRequest>();

  /** The landing order's two sections, in render order, each connected to the other. */
  protected readonly sectionOrder = [
    { key: 'ranked', label: 'Landing order', other: 'unranked' },
    { key: 'unranked', label: 'Unranked', other: 'ranked' },
  ] as const;

  /** The count waits for the reads, so a still-loading panel does not claim none. */
  protected readonly countShown = computed(() => this.state() === 'ready' || this.state() === 'empty');

  /** A dropped row, resolved (`resolveLandingDrop`) against the section it left and the one it landed in. A drop
   * that changes nothing writes nothing. */
  protected dropped(to: LandingSection, event: CdkDragDrop<LandingSection, LandingSection, string>): void {
    const sections = this.sections();
    if (sections === null) return;
    const request = resolveLandingDrop(sections, {
      key: event.item.data,
      from: event.previousContainer.data,
      previousIndex: event.previousIndex,
      to,
      currentIndex: event.currentIndex,
    });
    if (request) this.reposition.emit(request);
  }
}
