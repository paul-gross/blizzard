import { schema, validate } from '@angular/forms/signals';
import { projectLinkError, type ProjectLinkChoice, type ProjectLinkDraft } from './project-link.domain';

export function projectLinkSchema(choices: () => readonly ProjectLinkChoice[]) {
  return schema<ProjectLinkDraft>((p) => {
    validate(p.name, ({ value }) => {
      const message = projectLinkError({ name: value() }, choices());
      return message ? { kind: 'choice', message } : null;
    });
  });
}
