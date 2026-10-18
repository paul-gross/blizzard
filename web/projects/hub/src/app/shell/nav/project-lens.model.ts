import type { ProjectView } from 'fleet/shell';

/** One choice the project lens offers: All (`slug` null) or one project. */
export interface LensChoice {
  readonly slug: string | null;
  readonly label: string;
  /** The project's palette color; `null` for All. */
  readonly color: string | null;
}

/** The lens's choices: All, then `projects` by name. */
export function lensChoices(projects: readonly ProjectView[]): readonly LensChoice[] {
  return [
    { slug: null, label: 'All', color: null },
    ...[...projects]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((project) => ({ slug: project.slug, label: project.name, color: project.color })),
  ];
}
