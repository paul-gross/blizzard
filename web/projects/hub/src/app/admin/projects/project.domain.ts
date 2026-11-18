import type { Project } from '../../core/project.domain';

/** Editable project fields, independent of the transport. */
export interface ProjectDraft {
  slug: string;
  name: string;
  description: string;
}

export function projectDraft(project: Project | null): ProjectDraft {
  return { slug: project?.slug ?? '', name: project?.name ?? '', description: project?.description ?? '' };
}

export function normalizedProjectDraft(draft: ProjectDraft): ProjectDraft {
  return { slug: draft.slug.trim(), name: draft.name.trim(), description: draft.description.trim() };
}

export function projectSlugError(value: string): string | null {
  const slug = value.trim();
  return slug.length <= 64 && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)
    ? null : 'Use 1–64 lowercase letters, digits and single inner hyphens.';
}

export function projectNameError(value: string, create: boolean): string | null {
  const name = value.trim();
  return (create || name.length > 0) && Array.from(name).length <= 128
    ? null : 'Use a name of 1–128 characters.';
}

export function projectDraftErrors(draft: ProjectDraft, create: boolean): Partial<Record<keyof ProjectDraft, string>> {
  const slug = projectSlugError(draft.slug);
  const name = projectNameError(draft.name, create);
  return { ...(slug ? { slug } : {}), ...(name ? { name } : {}) };
}
