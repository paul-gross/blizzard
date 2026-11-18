import { schema, validate } from '@angular/forms/signals';
import { projectBranchError, type ProjectBranchDraft } from './project-branch.domain';

export const projectBranchSchema = schema<ProjectBranchDraft>((p) => {
  validate(p.baseBranch, ({ value }) => {
    const message = projectBranchError(value());
    return message ? { kind: 'branch', message } : null;
  });
});
