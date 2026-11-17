import { errorMessage, type ProjectView, type UrgencyView } from 'fleet';
import { urgencyOptions as buildUrgencyOptions } from '../../core/urgency-options.model';

export function projectOptions(projects: readonly ProjectView[]) {
  return projects.map((p) => ({ value: p.project_id, label: p.name }));
}
export function urgencyOptions(urgencies: readonly UrgencyView[]) {
  return buildUrgencyOptions(urgencies, { key: 'urgency_id', retired: 'exclude' });
}
/** The dialog's error line for a failed create — `null` while no create has failed. */
export function createErrorText(error: unknown): string | null {
  return error ? errorMessage(error, 'Could not create hub item.') : null;
}
