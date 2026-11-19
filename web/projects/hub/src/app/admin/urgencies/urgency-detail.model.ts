import type { UrgencyIconDataUrlState } from '../../core/urgency-icon.query';
import type { Urgency, ViewportMode } from 'fleet';
import { configActions, type ConfigActionsVm } from '../config-actions.model';
import type { UrgencyDraft } from './urgency.domain';
import type { ConfigRecordVm } from '../config-record-panel';

export function urgencyRecordVm(urgency: Urgency | undefined): ConfigRecordVm | null {
  if (!urgency) return null;
  return {
    name: urgency.name, badges: [], revision: urgency.revision,
    facts: [
      { label: 'Slug', value: urgency.slug }, { label: 'Color', value: urgency.color },
      { label: 'Open chunks', value: String(urgency.openChunks) }, { label: 'Default', value: urgency.default ? 'Yes' : 'No' },
    ],
    note: urgency.retired ? 'Retired' : null, links: null, hasHistory: true,
  };
}

export function urgencyActions(
  mode: ViewportMode, canEdit: boolean, canControlChunks: boolean, urgency: Urgency | undefined, shown: UrgencyDraft | null,
): ConfigActionsVm | null {
  const actions = configActions(mode, canEdit, urgency);
  if (!actions || !urgency) return null;
  return {
    ...actions, edit: shown !== null,
    retireBlocked: urgency.default ? 'Choose another default before retiring this urgency.' :
      urgency.openChunks > 0 && !canControlChunks ? 'Moving open chunks requires chunk control.' : null,
  };
}

export function urgencyDestinations(records: readonly Urgency[], urgencyId: string | undefined): readonly Urgency[] {
  return records.filter((record) => !record.retired && record.id !== urgencyId);
}

export function urgencyCliCommand(mode: ViewportMode, urgency: Urgency | undefined): string | null {
  return mode === 'mobile' && urgency ? `blizzard hub urgency edit ${urgency.slug}` : null;
}

/** A draft may use only a completed conversion of the currently addressed icon. */
export function urgencyImageUrl(state: UrgencyIconDataUrlState): string | null {
  return state.status === 'success' ? state.url : null;
}

/** Explain why image-backed editing is unavailable rather than presenting an empty mark. */
export function urgencyImageError(urgency: Urgency | undefined, actions: ConfigActionsVm | null, state: UrgencyIconDataUrlState): string | null {
  return urgency?.icon && actions && state.status === 'error'
    ? 'Could not load the urgency image. A new edit is unavailable.' : null;
}
