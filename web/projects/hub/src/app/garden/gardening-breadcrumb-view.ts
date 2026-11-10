import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitMenu, KitMenuItemRadio, KitMenuPanel } from 'fleet';

import { type GardenProjectChoice, waitingLabel } from './gardening-page.model';

/** The garden lens breadcrumb and its project-choice menu. */
@Component({
  selector: 'app-gardening-breadcrumb-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitMenu, KitMenuItemRadio, KitMenuPanel],
  templateUrl: './gardening-breadcrumb-view.html',
  styleUrl: './gardening-breadcrumb-view.css',
})
export class GardeningBreadcrumbView {
  readonly doorwayHref = input.required<string>();
  readonly projectLabel = input.required<string>();
  readonly checkedSlug = input.required<string | null>();
  readonly surfaceLabel = input.required<string | null>();
  readonly choices = input.required<readonly GardenProjectChoice[]>();
  readonly doorwayClick = output<MouseEvent>();
  readonly projectSelected = output<string | null>();
  protected readonly waitingLabel = waitingLabel;
}
