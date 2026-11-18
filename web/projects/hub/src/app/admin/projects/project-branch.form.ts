import { schema, validate } from '@angular/forms/signals';
import { projectBranchError, type ProjectBranchDraft } from './project-branch.domain';

export const projectBranchSchema = schema<ProjectBranchDraft>((p) => {
  validate(p.baseBranch, ({ value }) => {
    const message = projectBranchError(value());
    return message ? { kind: 'branch', message } : null;
  });
});

/** The branch dialog suppresses override validation while explicitly inheriting. */
export function branchDialogSchema(inheriting: () => boolean) {
  return schema<ProjectBranchDraft>((p) => {
    validate(p.baseBranch, ({ value }) => {
      const message = inheriting() ? null : projectBranchError(value());
      return message ? { kind: 'branch', message } : null;
    });
  });
}
