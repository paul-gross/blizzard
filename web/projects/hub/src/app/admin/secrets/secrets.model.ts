import type { SecretCreateRequestWritable, SecretReplaceRequest, SecretView } from 'fleet';

import { lifecycleTone } from '../../core/lifecycle-tone';
import { type ConfigFieldDef, createText } from '../config-edit.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { recordLink } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';

/** The create a new secret's form sends — its name and its write-only value. */
export function secretCreate(body: Readonly<Record<string, string | boolean>>): SecretCreateRequestWritable {
  return { name: createText(body, 'name'), value: createText(body, 'value') };
}

/** A secret's badges — retired, or unused when nothing live refers to it. */
export function secretBadges(secret: SecretView): readonly ConfigBadgeVm[] {
  if (secret.retired) return [{ label: 'retired', tone: lifecycleTone(true) }];
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
    sub: [referenceCount(secret)],
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
      { label: 'Replaced by', value: secret.replaced_by_name ?? secret.replaced_by },
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

/** A new secret's form fields — its name and its write-only value. */
export const SECRET_CREATE_FIELDS: readonly ConfigFieldDef<keyof SecretCreateRequestWritable>[] = [
  { key: 'name', label: 'Name', kind: 'text', required: true },
  { key: 'value', label: 'Value', kind: 'password', required: true },
];

/** The replace form's one field. */
export const SECRET_REPLACE_FIELDS: readonly ConfigFieldDef<keyof SecretReplaceRequest>[] = [
  { key: 'value', label: 'New value', kind: 'password', required: true },
];
