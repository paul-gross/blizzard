import type { SaveOutcome } from 'fleet/forms';
import { projectLinkError, type ProjectLinkChoice, type ProjectLinkDraft, type ProjectLinkKind } from './project-link.domain';
import { projectLinkRefusal, toProjectLink } from './project-link.translate';
import { injectProjectLinkMutation } from './projects.mutations';

export function injectProjectLinkService() {
  const mutation = injectProjectLinkMutation();
  return {
    pending: mutation.isPending,
    async link(projectId: string, kind: ProjectLinkKind, draft: ProjectLinkDraft, choices: readonly ProjectLinkChoice[]): Promise<SaveOutcome<ProjectLinkDraft>> {
      const message = projectLinkError(draft, choices);
      if (message) return { ok: false, fields: { name: message }, form: null };
      try {
        await mutation.mutateAsync(toProjectLink(projectId, kind, draft));
        return { ok: true };
      } catch (error) { return projectLinkRefusal(error); }
    },
  };
}
