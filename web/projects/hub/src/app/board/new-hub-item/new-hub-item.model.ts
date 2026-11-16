import type { ProjectView, UrgencyView } from 'fleet';
import { urgencyOptions as buildUrgencyOptions } from '../../core/urgency-options.model';

export function projectOptions(projects: readonly ProjectView[]) {
  return projects.map((p) => ({ value: p.project_id, label: p.name }));
}
export function urgencyOptions(urgencies: readonly UrgencyView[]) {
  return buildUrgencyOptions(urgencies, { key: 'urgency_id', retired: 'exclude' });
}
