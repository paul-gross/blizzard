import type { ConfigFieldView } from '../config-field-tree.model';
import type { ProjectDraft } from './project.domain';

export const PROJECT_FIELDS: readonly ConfigFieldView<ProjectDraft>[] = [
  { key: 'slug', label: 'Slug', kind: 'text' },
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'description', label: 'Description', kind: 'text' },
];
