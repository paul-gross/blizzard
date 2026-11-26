import { readRefusal } from 'fleet/core';
import { type RefusalMap, type SaveOutcome } from 'fleet/forms';
import { type ProjectCreateRequest, type ProjectPatchRequest, type ProjectView } from 'fleet/hub-api';
import { fromProjectView } from '../../core/project.translate';
import { normalizedProjectDraft, type ProjectDraft } from './project.domain';

export function toProjectCreate(draft: ProjectDraft): ProjectCreateRequest {
  const value = normalizedProjectDraft(draft);
  return { ...value, name: value.name || value.slug };
}

export function toProjectPatch(before: ProjectDraft, after: ProjectDraft): ProjectPatchRequest {
  const value = normalizedProjectDraft(after);
  const body: ProjectPatchRequest = {};
  if (value.slug !== before.slug) body.slug = value.slug;
  if (value.name !== before.name) body.name = value.name;
  if (value.description !== before.description) body.description = value.description;
  return body;
}

export function toProjectColorPatch(color: string): ProjectPatchRequest {
  return { color };
}

export const savedProject = (wire: ProjectView) => fromProjectView(wire);

const REFUSALS: RefusalMap<ProjectDraft> = {
  field: (path) => path === 'slug' || path === 'name' || path === 'description' ? path : null,
  kinds: { 'urn:blizzard:problem:project-slug-taken': 'slug' },
};

export function projectRefusal(error: unknown): SaveOutcome<ProjectDraft> {
  return readRefusal(error, REFUSALS);
}
