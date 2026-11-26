import type { SaveOutcome } from 'fleet/forms';
import { projectBranchError, type ProjectBranchBaseline, type ProjectBranchDraft } from './project-branch.domain';
import { projectBranchRefusal, toProjectBranch } from './project-branch.translate';
import { injectRepositoryBranchMutation } from './projects.mutations';

export function injectProjectBranchService() {
  const mutation = injectRepositoryBranchMutation();
  async function write(before: ProjectBranchBaseline, branch: string | null): Promise<SaveOutcome<ProjectBranchDraft>> {
    if (branch === before.override) return { ok: true };
    try {
      await mutation.mutateAsync(toProjectBranch(before, branch));
      return { ok: true };
    } catch (error) { return projectBranchRefusal(error); }
  }
  return {
    pending: mutation.isPending,
    save(before: ProjectBranchBaseline, draft: ProjectBranchDraft): Promise<SaveOutcome<ProjectBranchDraft>> {
      const message = projectBranchError(draft.baseBranch);
      if (message) return Promise.resolve({ ok: false, fields: { baseBranch: message }, form: null });
      return write(before, draft.baseBranch);
    },
    inherit(before: ProjectBranchBaseline): Promise<SaveOutcome<ProjectBranchDraft>> { return write(before, null); },
  };
}
