import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { projectCreateSchema, projectEditSchema } from './project.form';

describe('project schemas', () => {
  it('enforces the separate create and edit name rules', () => {
    TestBed.runInInjectionContext(() => {
      const draft = signal({ slug: 'valid', name: ' ', description: '' });
      const create = form(draft, projectCreateSchema);
      const edit = form(draft, projectEditSchema);
      expect(create().valid()).toBe(true);
      expect(edit.name().valid()).toBe(false);
      draft.set({ slug: 'bad--slug', name: 'Name', description: '' });
      expect(create.slug().valid()).toBe(false);
      expect(edit.name().valid()).toBe(true);
    });
  });
});
