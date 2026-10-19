import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';

import { PanelFilters } from '../../core/panel-filters';
import { injectPanelProjects } from '../../core/panel-reads';
import { injectHostRunnersQuery } from '../../host/host.query';
import { FilterChips } from './filter-chips';
import { projectFilterChoices, runnerFilterChoices } from './panel-filters.model';

/**
 * The panel's two filters — **Runner** (All, then every runner on the host, a halted one
 * offered but disabled, a braked one naming its brake) and **Project** (All, then every
 * project the host serves) — writing {@link PanelFilters}, which keeps the page and only
 * narrows what it shows. It closes the desktop tab strip (`AppNav`) and, on mobile, sits
 * under the titlebar as two compact rows (`variant="mobile"`).
 */
@Component({
  selector: 'app-panel-filters',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FilterChips],
  templateUrl: './panel-filters-bar.html',
  styleUrl: './panel-filters-bar.css',
  host: { '[class.mobile]': "variant() === 'mobile'" },
})
export class PanelFiltersBar {
  /** Where the filters sit — inline in the desktop tab strip, or as their own mobile rows. */
  readonly variant = input<'inline' | 'mobile'>('inline');

  protected readonly filters = inject(PanelFilters);
  private readonly runnersQuery = injectHostRunnersQuery();
  private readonly projects = injectPanelProjects();

  protected readonly runnerChoices = computed(() => runnerFilterChoices(this.runnersQuery.data() ?? []));
  protected readonly projectChoices = computed(() => projectFilterChoices(this.projects()));
}
