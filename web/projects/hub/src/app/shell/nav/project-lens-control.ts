import { ChangeDetectionStrategy, Component, computed, inject, input } from '@angular/core';

import { KitChip, type ProjectView } from 'fleet/shell';

import { ProjectLens } from '../../core/project-lens';
import { injectProjectsQuery } from '../../core/projects.query';

/** One choice the lens control offers: All (`slug` null) or one project. */
export interface LensChoice {
  readonly slug: string | null;
  readonly label: string;
}

/** The lens's choices: All, then `projects` by name. */
export function lensChoices(projects: readonly ProjectView[]): readonly LensChoice[] {
  return [
    { slug: null, label: 'All' },
    ...[...projects]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((project) => ({ slug: project.slug, label: project.name })),
  ];
}

/**
 * The project lens control in the view-tab row: **All**, then one chip per project of the tenant, the lens's own
 * choice selected. Choosing re-navigates the view the board is on under that lens ({@link ProjectLens.switchTo}).
 *
 * The chips scroll inside the control rather than widen the row, so a tenant with many projects never pushes the tab
 * row past the viewport. The tenant is not here: it changes only from the profile menu.
 */
@Component({
  selector: 'app-project-lens-control',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitChip],
  templateUrl: './project-lens-control.html',
  styleUrl: './project-lens-control.css',
})
export class ProjectLensControl {
  /** Whether the control reads the tenant's projects — the shell's, once it has entered its tenant. */
  readonly enabled = input(true);

  private readonly lens = inject(ProjectLens);
  private readonly projectsQuery = injectProjectsQuery(() => this.enabled());

  /** The slug the lens is on, `null` for All. */
  protected readonly current = this.lens.slug;

  /** All, then the tenant's projects by name. */
  protected readonly choices = computed(() => lensChoices(this.projectsQuery.data() ?? []));

  protected choose(choice: LensChoice): void {
    if (choice.slug === this.current()) return;
    void this.lens.switchTo(choice.slug);
  }
}
