import { lifecycleTone } from '../../core/lifecycle-tone';
import type { ConfigFieldView } from '../config-field-tree.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { recordLink } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';
import type { Secret, SecretCreateDraft, SecretReplaceDraft } from './secret.domain';
import { secretReferenceRefs } from './secret.translate';

/** A secret's badges — retired, or unused when nothing live refers to it. */
export function secretBadges(secret: Secret): readonly ConfigBadgeVm[] {
  if (secret.retired) return [{ label: 'retired', tone: lifecycleTone(true) }];
  return secret.references.length === 0 ? [{ label: 'unused', tone: 'waiting' }] : [];
}

/** How many records refer to a secret, as text. */
export function referenceCount(secret: Secret): string {
  const count = secret.references.length;
  return `${count} reference${count === 1 ? '' : 's'}`;
}

/** The secret list's rows under `filter`. */
export function secretRows(secrets: readonly Secret[], filter: LifecycleFilter): readonly ConfigRowVm[] {
  return filterByLifecycle(secrets, filter).map((secret) => ({
    key: secret.name,
    title: secret.name,
    sub: [referenceCount(secret)],
    badges: secretBadges(secret),
    revision: secret.revision,
    retired: secret.retired,
  }));
}

/** A secret's detail — `null` with none loaded. The value never reaches the board, so
 * there is none to show. */
export function secretRecordVm(secret: Secret | undefined): ConfigRecordVm | null {
  if (!secret) return null;
  return {
    name: secret.name,
    badges: secretBadges(secret),
    facts: [
      { label: 'Value', value: '•••••••• write-only, never shown' },
      { label: 'Replaced by', value: secret.replacedByName ?? secret.replacedBy },
    ],
    revision: secret.revision,
    note: null,
    links: {
      heading: 'Referred to by',
      links: secretReferenceRefs(secret).map(recordLink),
      emptyText: 'Nothing refers to this secret.',
    },
    hasHistory: true,
  };
}

/** The create form's fields, presentation only: the rules say both are required. The value is masked multi-line text. */
export const SECRET_CREATE_FIELD_VIEWS: readonly ConfigFieldView<SecretCreateDraft>[] = [
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'value', label: 'Value', kind: 'secret-text' },
];

/** The replace form's one field. */
export const SECRET_REPLACE_FIELD_VIEWS: readonly ConfigFieldView<SecretReplaceDraft>[] = [
  { key: 'value', label: 'New value', kind: 'secret-text' },
];
