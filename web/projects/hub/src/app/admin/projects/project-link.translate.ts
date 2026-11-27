import { readRefusal } from 'fleet/core';
import { type RefusalMap, type SaveOutcome } from 'fleet/forms';
import type { ProjectLinkDraft, ProjectLinkKind } from './project-link.domain';
import type { ProjectLinkVars } from './projects.mutations';

export function toProjectLink(projectId: string, kind: ProjectLinkKind, draft: ProjectLinkDraft): ProjectLinkVars {
  return { projectId, kind, name: draft.name, linked: true, revision: null };
}
const REFUSALS: RefusalMap<ProjectLinkDraft> = { field: (path) => path === 'name' ? 'name' : null, kinds: {} };
export function projectLinkRefusal(error: unknown): SaveOutcome<ProjectLinkDraft> {
  return readRefusal(error, REFUSALS);
}
