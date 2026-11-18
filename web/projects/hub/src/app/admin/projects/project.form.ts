import { schema, validate } from '@angular/forms/signals';
import { projectNameError, projectSlugError, type ProjectDraft } from './project.domain';

function rules(create: boolean) {
  return schema<ProjectDraft>((p) => {
    validate(p.slug, ({ value }) => {
      const message = projectSlugError(value());
      return message ? { kind: 'slug', message } : null;
    });
    validate(p.name, ({ value }) => {
      const message = projectNameError(value(), create);
      return message ? { kind: 'name', message } : null;
    });
  });
}

export const projectCreateSchema = rules(true);
export const projectEditSchema = rules(false);
