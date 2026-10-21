import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitButton, KitPanel, KitPanelHeader } from 'fleet';

import type { OverviewLineVm } from './project-overview.model';

/**
 * One card of a project's overview — a heading over the records it names (a linked source, a
 * linked repository, a serving runner) and what each is, or a line saying there are none. A card
 * whose records are edited on another tab offers an "open" control for it. Presentational.
 */
@Component({
  selector: 'app-project-overview-card',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton, KitPanel, KitPanelHeader],
  templateUrl: './project-overview-card.html',
  styleUrl: './project-overview-card.css',
})
export class ProjectOverviewCard {
  readonly heading = input.required<string>();
  readonly lines = input.required<readonly OverviewLineVm[]>();
  /** What the lines rest on: they render once it is ready, and the card says it has none only once its reads settled empty. */
  readonly state = input<KitAsyncStateValue>('ready');
  /** What the card says with no records. */
  readonly none = input.required<string>();
  /** Whether the card offers to open the tab its records are edited on. */
  readonly opens = input(false);
  readonly testid = input.required<string>();

  readonly open = output<void>();
}
