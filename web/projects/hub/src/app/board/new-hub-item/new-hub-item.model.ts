import type { ProjectView, UrgencyView } from 'fleet';

export function projectOptions(projects: readonly ProjectView[]) {
  return projects.map((p) => ({ value: p.project_id, label: p.name }));
}
export function urgencyOptions(urgencies: readonly UrgencyView[]) {
  return urgencies.filter((u) => !u.retired).map((u) => ({ value: u.urgency_id, label: u.name }));
}
