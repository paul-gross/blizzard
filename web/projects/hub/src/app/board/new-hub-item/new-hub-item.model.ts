import { type Urgency } from 'fleet/core';
import type { Project } from '../../core/project.domain';
import { urgencyOptions as buildUrgencyOptions } from '../../core/urgency-options.model';

export function projectOptions(projects: readonly Project[]) {
  return projects.map((p) => ({ value: p.project_id, label: p.name }));
}
export function urgencyOptions(urgencies: readonly Urgency[]) {
  return buildUrgencyOptions(urgencies, { key: 'id', retired: 'exclude' });
}
