import type { CredentialChange, CredentialDraft, CredentialForgeBindings } from './credential.domain';
import { type CredentialEditBaseline, toEditRequest } from './credential.translate';
import type { ConfigFieldView } from '../config-field-tree.model';
import type { ForgeChoice } from '../repositories/repository.domain';
import { type Tone, errorMessage } from 'fleet';
import type { Credential, CredentialCheck, CredentialCheckOperation, CredentialCheckTarget, CredentialCheckUse, CredentialDocument, CredentialListItem } from './credential.domain';
export type { CredentialCheckTarget } from './credential.domain';

import { lifecycleTone } from '../../core/lifecycle-tone';
import { type ConfigFieldDef, type ConfigFieldOption } from '../config-edit.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { recordLink } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';
import type { CredentialChoice } from '../repositories/repository.domain';

export function credentialBadges(credential: Credential): readonly ConfigBadgeVm[] {
  return credential.retired ? [{ label: 'retired', tone: lifecycleTone(true) }] : [];
}

/** How many records name a credential, as the list shows it. */
export function referrerCountText(count: number): string {
  return count === 1 ? 'used by 1 record' : `used by ${count} records`;
}

/** The credential list's rows under `filter`. */
export function credentialRows(credentials: readonly CredentialListItem[], filter: LifecycleFilter): readonly ConfigRowVm[] {
  return filterByLifecycle(credentials, filter).map((credential) => ({
    key: credential.id,
    title: credential.slug,
    sub: [
      ...(credential.name === credential.slug ? [] : [credential.name]),
      credential.forgeSlug,
      credential.binding,
      referrerCountText(credential.referrerCount),
    ],
    badges: credentialBadges(credential),
    revision: credential.revision,
    retired: credential.retired,
  }));
}

/** A setting's value as text. */
function settingText(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** A credential's detail — `null` with none loaded. Its authority is the identity it acts as,
 * derived by the hub from its binding, secret and settings, and never edited. */
export function credentialRecordVm(credential: CredentialDocument | undefined): ConfigRecordVm | null {
  if (!credential) return null;
  return {
    name: credential.slug,
    badges: credentialBadges(credential),
    revision: credential.revision,
    facts: [
      { label: 'Name', value: credential.name },
      { label: 'Forge', value: credential.forgeName },
      { label: 'Binding', value: credential.binding },
      { label: 'Secret', value: credential.secretName },
      { label: 'Authority', value: credential.authority },
      ...Object.entries(credential.settings).map(([key, value]) => ({ label: key, value: settingText(value) })),
      { label: 'Created by', value: credential.createdByName ?? credential.createdBy },
    ],
    note: credential.retired ? 'Retired — no record may newly name it.' : null,
    links: {
      heading: 'Used by',
      links: credential.referrers.map((ref) => recordLink({ kind: ref.kind, key: ref.key, label: ref.label })),
      emptyText: 'No repositories or work sources use this credential.',
    },
    hasHistory: true,
  };
}

/** The credential choices a repository or work source form offers: each enabled credential, keyed by its
 * forge, so the select offers only the picked forge's credentials and a forge change clears the pick. */
export function credentialOptions(credentials: readonly CredentialChoice[]): readonly ConfigFieldOption[] {
  return credentials
    .filter((credential) => !credential.retired)
    .map((credential) => ({ value: credential.id, label: `${credential.slug} · ${credential.binding}`, kind: credential.forgeId }));
}

/** `fields` with its `credential` select's choices filled in. */
export function credentialPickerFields<K extends string>(fields: readonly ConfigFieldDef<K>[], credentials: readonly CredentialChoice[]): readonly ConfigFieldDef<K>[] {
  return fields.map((field) => (field.key === 'credential' ? { ...field, options: credentialOptions(credentials) } : field));
}

/** One operation of a credential check, as the check table shows it. */
export interface CheckOperationVm {
  readonly operation: string;
  readonly outcome: string;
  readonly tone: Tone;
  readonly detail: string | null;
  /** The forge's sanitized refusing answer — its status, call, and words — or `null`. */
  readonly diagnostic: string | null;
}

/** One repository or work source a check answered. */
export interface CheckUseVm {
  readonly key: string;
  readonly heading: string;
  /** `false` when the check ended before this use was wholly answered. */
  readonly reached: boolean;
  readonly operations: readonly CheckOperationVm[];
}

/** A credential check's answer, as the check table shows it. */
export interface CredentialCheckVm {
  readonly mint: CheckOperationVm | null;
  readonly identity: string;
  /** The warning shown when the declared variant disagrees with the token itself, else `null`. */
  readonly variantWarning: string | null;
  readonly uses: readonly CheckUseVm[];
  /** Why the check reached no use, when the forge gave no answer to building the credential's authorization. */
  readonly unreachedReason: string | null;
  readonly emptyText: string;
  readonly notEvaluated: string;
}

/** The tone each check outcome shows in: a refusal needs a person, an unsupported capability waits on a credential
 * change, and an unproven write is neither shown nor refused. */
export function checkOutcomeTone(outcome: CredentialCheckOperation['outcome']): Tone {
  switch (outcome) {
    case 'proven':
      return 'done';
    case 'granted':
      return 'spawning';
    case 'unproven':
      return 'idle';
    case 'refused':
      return 'needs';
    case 'unsupported':
      return 'waiting';
    default:
      return 'idle';
  }
}

/** What a check heads a use with: its kind, as an operator reads it; a kind this build does not know, as sent. */
function checkUseNoun(kind: CredentialCheckUse['kind']): string {
  switch (kind) {
    case 'repository':
      return 'repository';
    case 'work_source':
      return 'work source';
    default:
      return kind;
  }
}

function checkOperationVm(result: CredentialCheckOperation): CheckOperationVm {
  const diagnostic = result.diagnostic;
  return {
    operation: result.operation,
    outcome: result.outcome,
    tone: checkOutcomeTone(result.outcome),
    detail: result.detail,
    diagnostic: diagnostic && `${diagnostic.status} ${diagnostic.endpoint}: ${diagnostic.message}`,
  };
}

/** The check table's view of `POST /api/credentials/{credential}/checks`'s answer. */
export function credentialCheckVm(check: CredentialCheck): CredentialCheckVm {
  return {
    mint: check.mint && checkOperationVm(check.mint),
    identity: check.variant ? `${check.binding} · ${check.variant}` : check.binding,
    variantWarning: check.variantContradicted ? 'The declared variant disagrees with the token itself.' : null,
    uses: check.uses.map((use) => ({
      key: `${use.kind}:${use.id}`,
      heading: `${checkUseNoun(use.kind)} ${use.slug}`,
      reached: use.reached,
      operations: use.operations.map(checkOperationVm),
    })),
    unreachedReason: check.unreachedReason,
    emptyText: 'No enabled repository or work source uses this credential.',
    notEvaluated: check.notEvaluated,
  };
}

/** The Check action a record offers — `null` unless the viewer may edit config and the record names a credential.
 * With no `use` the credential is checked against every record acting with it; with one, against that record alone. */
export function credentialCheckTarget(
  canEdit: boolean,
  credentialId: string | null | undefined,
  use?: { readonly field: 'repositories' | 'work_sources'; readonly id: string | null | undefined },
): CredentialCheckTarget | null {
  if (!canEdit || !credentialId) return null;
  if (!use) return { credentialId, scope: {} };
  return use.id ? { credentialId, scope: { [use.field]: [use.id] } } : null;
}

/** Whether a check's variables are for `target` — an answer or failure for another credential or scope is not shown. */
export function isCheckFor(vars: CredentialCheckTarget | undefined, target: CredentialCheckTarget): boolean {
  return vars !== undefined && vars.credentialId === target.credentialId && JSON.stringify(vars.scope) === JSON.stringify(target.scope);
}

/** The check table's view of an answer, when it is for the target shown. */
export function shownCheckVm(answer: CredentialCheck | undefined, current: boolean): CredentialCheckVm | null {
  return answer && current ? credentialCheckVm(answer) : null;
}

/** The failure line of a check call, when it failed for the target shown. */
export function checkFailure(failed: boolean, error: unknown, current: boolean): string | null {
  return failed && current ? errorMessage(error, 'Check failed.') : null;
}


/** Create-field presentation, with bindings filtered by the selected forge. */
export function credentialCreateFields(forges: readonly ForgeChoice[], bindings: readonly CredentialForgeBindings[]): readonly ConfigFieldView<CredentialDraft>[] {
  return [
    { key: 'slug', label: 'Slug', kind: 'text' },
    { key: 'name', label: 'Name', kind: 'text' },
    { key: 'forge', label: 'Forge', kind: 'select', createOnly: true, options: forges.filter((f) => !f.retired).map((f) => ({ value: f.id, label: f.slug })) },
    { key: 'binding', label: 'Binding', kind: 'select', createOnly: true, filterBy: 'forge', options: bindings.flatMap((f) => f.bindings.map((name) => ({ value: name, label: name, kind: f.forgeId }))) },
    { key: 'secret', label: 'Secret', kind: 'text' },
  ];
}

export function credentialSettingsUnavailable(baseline: CredentialEditBaseline | null): boolean {
  return baseline !== null && !baseline.bindings.some((binding) => binding.name === baseline.draft.binding);
}

/** The displayed diff uses the exact same request judgment as save. */
export function credentialEditChanges(baseline: CredentialEditBaseline | null, draft: CredentialDraft): readonly CredentialChange[] {
  if (!baseline) return [];
  const result = toEditRequest(baseline, draft);
  if (!result.ok) return [{ field: 'Settings', old: null, new: result.form, old_label: null, new_label: null }];
  const changes: CredentialChange[] = [];
  for (const key of ['slug', 'name', 'secret'] as const) {
    if (key in result.request) changes.push({ field: key === 'slug' ? 'Slug' : key === 'name' ? 'Name' : 'Secret', old: baseline.draft[key] || null, new: result.request[key] || null, old_label: null, new_label: null });
  }
  if (result.request.settings !== undefined) {
    const next = result.request.settings ?? {};
    for (const key of new Set([...Object.keys(baseline.storedSettings), ...Object.keys(next)])) {
      const before = baseline.storedSettings[key];
      const after = next[key];
      if (JSON.stringify(before) !== JSON.stringify(after)) changes.push({ field: key, old: before ?? null, new: after ?? null, old_label: null, new_label: null });
    }
  }
  return changes;
}
