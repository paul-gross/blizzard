import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { KitMenuItemRadio, KitMenuPanel } from 'fleet';

import { ProjectLens } from '../../core/project-lens';
import { injectProjectsQuery } from '../../core/projects.query';
import { type LensChoice, lensChoices } from './project-lens-control';

/**
 * The mobile titlebar's project lens submenu — **All**, then one radio item per project of the tenant, the lens's own
 * choice checked; choosing re-navigates the view the board is on under that lens, as the desktop lens control does.
 */
@Component({
  selector: 'app-project-menu',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitMenuItemRadio, KitMenuPanel],
  templateUrl: './project-menu.html',
})
export class ProjectMenu {
  private readonly lens = inject(ProjectLens);
  private readonly projectsQuery = injectProjectsQuery();

  /** The slug the lens is on, `null` for All. */
  protected readonly current = this.lens.slug;

  /** All, then the tenant's projects by name. */
  protected readonly choices = computed(() => lensChoices(this.projectsQuery.data() ?? []));

  protected choose(choice: LensChoice): void {
    if (choice.slug === this.current()) return;
    void this.lens.switchTo(choice.slug);
  }
}
