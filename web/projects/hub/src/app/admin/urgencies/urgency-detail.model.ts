import type { UrgencyView, ViewportMode } from 'fleet';
import { configActions, type ConfigActionsVm } from '../config-actions.model';
import type { ConfigFormValues } from '../config-edit.model';
import type { ConfigRecordVm } from '../config-record-panel';

export function urgencyRecordVm(urgency: UrgencyView | undefined): ConfigRecordVm | null {
  if (!urgency) return null;
  return {
    name: urgency.name, badges: [], revision: urgency.revision,
    facts: [
      { label: 'Slug', value: urgency.slug }, { label: 'Color', value: urgency.color },
      { label: 'Open chunks', value: String(urgency.open_chunks) }, { label: 'Default', value: urgency.default ? 'Yes' : 'No' },
    ],
    note: urgency.retired ? 'Retired' : null, links: null, hasHistory: true,
  };
}

export function urgencyFormValues(urgency: UrgencyView | undefined, image: string | null | undefined): ConfigFormValues | null {
  if (!urgency || (urgency.icon && !image)) return null;
  return { slug: urgency.slug, name: urgency.name, color: urgency.color, mark: urgency.emoji ?? (urgency.icon ? image! : '') };
}

export function urgencyActions(
  mode: ViewportMode, canEdit: boolean, canControlChunks: boolean, urgency: UrgencyView | undefined, shown: ConfigFormValues | null,
): ConfigActionsVm | null {
  const actions = configActions(mode, canEdit, urgency);
  if (!actions || !urgency) return null;
  return {
    ...actions, edit: shown !== null,
    retireBlocked: urgency.default ? 'Choose another default before retiring this urgency.' :
      urgency.open_chunks > 0 && !canControlChunks ? 'Moving open chunks requires chunk control.' : null,
  };
}

export function urgencyDestinations(records: readonly UrgencyView[], urgencyId: string | undefined): readonly UrgencyView[] {
  return records.filter((record) => !record.retired && record.urgency_id !== urgencyId);
}

export function urgencyCliCommand(mode: ViewportMode, urgency: UrgencyView | undefined): string | null {
  return mode === 'mobile' && urgency ? `blizzard hub urgency edit ${urgency.slug}` : null;
}
