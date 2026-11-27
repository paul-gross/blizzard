import type { EditBaseline, SaveOutcome } from 'fleet/forms';
import type { Project } from '../../core/project.domain';
import { projectDraftErrors, type ProjectDraft } from './project.domain';
import { projectRefusal, savedProject, toProjectColorPatch, toProjectCreate, toProjectPatch } from './project.translate';
import { injectCreateProjectMutation, injectEditProjectMutation } from './projects.mutations';

/** The callback receives a domain read only after the mutation and its invalidations settle. */
export function injectProjectService() {
  const createMutation = injectCreateProjectMutation();
  const editMutation = injectEditProjectMutation();
  return {
    createPending: createMutation.isPending,
    editPending: editMutation.isPending,
    async create(draft: ProjectDraft, onSuccess: (project: Project) => void): Promise<SaveOutcome<ProjectDraft>> {
      const fields = projectDraftErrors(draft, true);
      if (Object.keys(fields).length) return { ok: false, fields, form: null };
      try {
        const result = await createMutation.mutateAsync(toProjectCreate(draft));
        onSuccess(savedProject(result));
        return { ok: true };
      } catch (error) { return projectRefusal(error); }
    },
    async edit(before: EditBaseline<ProjectDraft>, draft: ProjectDraft, onSuccess: (project: Project) => void): Promise<SaveOutcome<ProjectDraft>> {
      const fields = projectDraftErrors(draft, false);
      if (Object.keys(fields).length) return { ok: false, fields, form: null };
      try {
        const result = await editMutation.mutateAsync({ projectId: before.id, revision: before.revision, body: toProjectPatch(before.draft, draft) });
        onSuccess(savedProject(result));
        return { ok: true };
      } catch (error) { return projectRefusal(error); }
    },
    async color(projectId: string, revision: number, color: string): Promise<SaveOutcome<ProjectDraft>> {
      try {
        await editMutation.mutateAsync({ projectId, revision, body: toProjectColorPatch(color) });
        return { ok: true };
      } catch (error) { return projectRefusal(error); }
    },
  };
}
