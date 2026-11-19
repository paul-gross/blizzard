import { type ProjectView, type Urgency } from 'fleet';
import { urgencyOptions as buildUrgencyOptions } from '../../core/urgency-options.model';

export function projectOptions(projects: readonly ProjectView[]) {
  return projects.map((p) => ({ value: p.project_id, label: p.name }));
}
export function urgencyOptions(urgencies: readonly Urgency[]) {
  return buildUrgencyOptions(urgencies, { key: 'id', retired: 'exclude' });
}
