import { readRefusal } from 'fleet/core';
import { type RefusalMap, type SaveOutcome } from 'fleet/forms';
import type { ProjectBranchBaseline, ProjectBranchDraft } from './project-branch.domain';
import type { RepositoryBranchVars } from './projects.mutations';

export function toProjectBranch(before: ProjectBranchBaseline, baseBranch: string | null): RepositoryBranchVars {
  return { projectId: before.projectId, repositoryId: before.id, revision: before.revision, baseBranch };
}
const REFUSALS: RefusalMap<ProjectBranchDraft> = { field: (path) => path === 'base_branch' ? 'baseBranch' : null, kinds: {} };
export function projectBranchRefusal(error: unknown): SaveOutcome<ProjectBranchDraft> {
  return readRefusal(error, REFUSALS);
}
