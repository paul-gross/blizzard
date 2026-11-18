import { createEnvironmentInjector, DestroyRef, EnvironmentInjector, inject, runInInjectionContext, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';
import { createRefusalState } from 'fleet';
import { projectLinkDraft, type ProjectLinkChoice, type ProjectLinkDraft } from './project-link.domain';
import { projectLinkSchema } from './project-link.form';

export function createProjectLinkForm(choices: () => readonly ProjectLinkChoice[]) {
  const draft = signal(projectLinkDraft(choices()));
  const refusal = createRefusalState<ProjectLinkDraft>();
  const linkForm = form(draft, (p) => {
    apply(p, projectLinkSchema(choices));
    apply(p, refusal.schema);
  });
  return { form: linkForm, refusal };
}

export function injectProjectLinkFormFactory() {
  const parent = inject(EnvironmentInjector);
  const destroyRef = inject(DestroyRef);
  let injector = createEnvironmentInjector([], parent);
  destroyRef.onDestroy(() => injector.destroy());
  return (choices: () => readonly ProjectLinkChoice[]) => {
    injector.destroy();
    injector = createEnvironmentInjector([], parent);
    return runInInjectionContext(injector, () => createProjectLinkForm(choices));
  };
}
