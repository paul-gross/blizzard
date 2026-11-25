import { formatAbsolute } from 'fleet';
import { lifecycleTone } from '../../core/lifecycle-tone';
import type { ConfigFieldView } from '../config-field-tree.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { factText, recordLink } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';
import type { Forge, ForgeBudget, ForgeDraft, ForgeRecord } from '../../forge/forge.domain';

/** How the forge form draws its fields — presentation only; the rules and the translator own the rest. */
export const FORGE_FIELD_VIEWS: readonly ConfigFieldView<ForgeDraft>[] = [
  { key: 'slug', label: 'Slug', kind: 'text' },
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'kind', label: 'Kind', kind: 'text', createOnly: true },
  { key: 'apiBase', label: 'API base', kind: 'text' },
  { key: 'webBase', label: 'Web base', kind: 'text' },
];

export function forgeBadges(forge: Forge): readonly ConfigBadgeVm[] {
  return forge.retired ? [{ label: 'retired', tone: lifecycleTone(true) }] : [];
}

export function forgeRows(forges: readonly Forge[], filter: LifecycleFilter): readonly ConfigRowVm[] {
  return filterByLifecycle(forges, filter).map((forge) => ({
    key: forge.id, title: forge.slug,
    sub: [forge.name, forge.kind, forge.apiBase],
    badges: forgeBadges(forge), revision: forge.revision, retired: forge.retired,
  }));
}

/** One fact per budget reading — bucket, the secret it spends if any, what remains of
 * its limit, and when it resets. The hub keeps readings since it started, so none may
 * have been taken yet. */
export function budgetFacts(budgets: readonly ForgeBudget[]): readonly { label: string; value: string }[] {
  if (budgets.length === 0) return [{ label: 'Budgets', value: 'no reading yet' }];
  return budgets.map((budget) => ({
    label: budget.secretName ? `Budget ${budget.bucket} · ${budget.secretName}` : `Budget ${budget.bucket}`,
    value: `${budget.remaining}/${budget.limit} remaining, resets ${formatAbsolute(budget.reset) || budget.reset}`,
  }));
}

export function forgeRecordVm(forge: ForgeRecord | undefined): ConfigRecordVm | null {
  if (!forge) return null;
  return {
    name: forge.slug, badges: forgeBadges(forge), revision: forge.revision,
    facts: [
      { label: 'Name', value: forge.name }, { label: 'Kind', value: forge.kind },
      { label: 'API base', value: forge.apiBase }, { label: 'Web base', value: factText(forge.webBase) },
      { label: 'Web origin', value: forge.webOrigin },
      { label: 'Created by', value: forge.createdByName ?? forge.createdBy },
      { label: 'Capabilities', value: forge.offered.length === 0 ? 'none' : forge.offered.join(', ') },
      ...budgetFacts(forge.budgets),
    ],
    note: forge.retired ? 'Retired — unavailable for new records.' : null,
    links: {
      heading: 'Used by',
      links: [
        ...forge.repositories.map((ref) => recordLink({ ...ref, kind: 'repository' })),
        ...forge.workSources.map((ref) => recordLink({ ...ref, kind: 'work_source' })),
      ],
      emptyText: 'No repositories or work sources use this forge.',
    },
    hasHistory: true,
  };
}

/** Only enabled forges are offered. A source's provider must match the forge's kind. */
export function forgeOptions(
  forges: readonly Forge[],
  provider?: string,
): readonly { value: string; label: string; kind: string }[] {
  return forges
    .filter((forge) => !forge.retired && (provider === undefined || forge.kind === provider))
    .map((forge) => ({ value: forge.id, label: `${forge.slug} · ${forge.name}`, kind: forge.kind }));
}
