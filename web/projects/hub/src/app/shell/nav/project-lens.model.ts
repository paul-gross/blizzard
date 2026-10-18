import type { ProjectView } from 'fleet/shell';

/** One choice the project lens offers: All (`slug` null) or one project. */
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
