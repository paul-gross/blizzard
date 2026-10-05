import type { SecretView } from 'fleet';

import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { recordLink } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';

/** A secret's badges — retired, or unused when nothing live refers to it. */
export function secretBadges(secret: SecretView): readonly ConfigBadgeVm[] {
  if (secret.retired) return [{ label: 'retired', tone: 'idle' }];
  return (secret.references ?? []).length === 0 ? [{ label: 'unused', tone: 'waiting' }] : [];
}

/** How many records refer to a secret, as text. */
export function referenceCount(secret: SecretView): string {
  const count = (secret.references ?? []).length;
  return `${count} reference${count === 1 ? '' : 's'}`;
}

/** The secret list's rows under `filter`. */
export function secretRows(secrets: readonly SecretView[], filter: LifecycleFilter): readonly ConfigRowVm[] {
  return filterByLifecycle(secrets, filter).map((secret) => ({
    key: secret.name,
    title: secret.name,
    sub: [`replaced by ${secret.replaced_by}`, referenceCount(secret)],
    badges: secretBadges(secret),
    revision: secret.revision,
    retired: secret.retired ?? false,
  }));
}

/** A secret's detail — `null` with none loaded. The value never reaches the board, so
 * there is none to show. */
export function secretRecordVm(secret: SecretView | undefined): ConfigRecordVm | null {
  if (!secret) return null;
  return {
    name: secret.name,
    badges: secretBadges(secret),
    facts: [
      { label: 'Value', value: '•••••••• write-only, never shown' },
      { label: 'Replaced by', value: secret.replaced_by },
    ],
    revision: secret.revision,
    note: null,
    links: {
      heading: 'Referred to by',
      links: (secret.references ?? []).map(recordLink),
      emptyText: 'Nothing refers to this secret.',
    },
    hasHistory: true,
  };
}
