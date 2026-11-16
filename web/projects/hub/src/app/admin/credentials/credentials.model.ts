import {
  type CredentialBindingView,
  type CredentialCheckRequest,
  type CredentialCheckView,
  type CredentialSettingView,
  type CredentialCreateRequest,
  type CredentialDocument,
  type CredentialListItem,
  type CredentialPatchRequest,
  type CredentialSummary,
  type ForgeBindingsView,
  type ForgeSummary,
  type OperationResultView,
  type Tone,
  errorMessage,
  unlisted,
} from 'fleet';

import { lifecycleTone } from '../../core/lifecycle-tone';
import { type ConfigFieldDef, type ConfigFieldOption, type ConfigFormValues, createText } from '../config-edit.model';
import { filterByLifecycle, type LifecycleFilter } from '../config-filter.model';
import { recordLink } from '../config-links.model';
import type { ConfigBadgeVm, ConfigRowVm } from '../config-record-list';
import type { ConfigRecordVm } from '../config-record-panel';
import { forgeOptions } from '../forges/forges.model';

/** The form key of one binding's setting — each binding's settings are their own fields, shown only
 * while that binding is picked. */
export function settingField(binding: string, key: string): string {
  return `setting.${binding}.${key}`;
}

/** The binding choices: for each enabled forge, every binding the hub allows on it, keyed by the
 * forge, so the select offers only the picked forge's bindings and a forge change clears the pick. */
export function bindingOptions(forgeBindings: readonly ForgeBindingsView[]): readonly ConfigFieldOption[] {
  return forgeBindings.flatMap((forge) => forge.bindings.map((name) => ({ value: name, label: name, kind: forge.forge_id })));
}

/** How a setting is entered: a select of its choices, a whole-number field, or plain text for a
 * kind this build does not know. */
function settingInput(setting: CredentialSettingView): Pick<ConfigFieldDef, 'kind' | 'options'> {
  switch (setting.kind) {
    case 'choice':
      return { kind: 'select', options: setting.choices.map((choice) => ({ value: choice, label: choice })) };
    case 'positive-integer':
      return { kind: 'number' };
    default:
      unlisted(setting.kind);
      return { kind: 'text' };
  }
}

/** A setting's form text as the wire takes it — a whole-number setting as a number. */
function settingValue(setting: CredentialSettingView, text: string): string | number {
  return setting.kind === 'positive-integer' ? Number(text) : text;
}

/** A credential's form fields. The forge and binding are fixed at create; each binding's settings
 * are entered as their kind says, shown only while that binding is picked. */
export function credentialFields(
  forges: readonly ForgeSummary[],
  bindings: readonly CredentialBindingView[],
  forgeBindings: readonly ForgeBindingsView[],
): readonly ConfigFieldDef[] {
  return [
    { key: 'slug', label: 'Slug', kind: 'text', required: true },
    { key: 'name', label: 'Name', kind: 'text' },
    { key: 'forge', label: 'Forge', kind: 'select', required: true, createOnly: true, options: forgeOptions(forges) },
    { key: 'binding', label: 'Binding', kind: 'select', required: true, createOnly: true, filterBy: 'forge', options: bindingOptions(forgeBindings) },
    { key: 'secret', label: 'Secret', kind: 'text', required: true },
    ...bindings.flatMap((binding) =>
      binding.settings.map((setting): ConfigFieldDef => ({
        key: settingField(binding.name, setting.key),
        label: setting.key,
        ...settingInput(setting),
        shownWhen: { key: 'binding', value: binding.name },
      })),
    ),
  ];
}

/** The settings the form holds for `binding` — each one left empty is omitted. */
function formSettings(values: Readonly<Record<string, string | boolean>>, binding: string, bindings: readonly CredentialBindingView[]): Record<string, string | number> {
  const settings: Record<string, string | number> = {};
  for (const setting of bindings.find((candidate) => candidate.name === binding)?.settings ?? []) {
    const value = values[settingField(binding, setting.key)];
    if (typeof value === 'string' && value.trim() !== '') settings[setting.key] = settingValue(setting, value.trim());
  }
  return settings;
}

/** The create a new credential's form sends: a name left empty is omitted, and the hub takes the slug. */
export function credentialCreate(body: Readonly<Record<string, string | boolean>>, bindings: readonly CredentialBindingView[]): CredentialCreateRequest {
  const text = (key: string): string => createText(body, key);
  const settings = formSettings(body, text('binding'), bindings);
  return {
    slug: text('slug'),
    ...(text('name') === '' ? {} : { name: text('name') }),
    forge: text('forge'),
    binding: text('binding'),
    secret: text('secret'),
    ...(Object.keys(settings).length === 0 ? {} : { settings }),
  };
}

/** A stored credential as its form shows it — its forge by id, its secret's name, each setting under its field. */
export function credentialShown(credential: CredentialSummary): Record<string, unknown> {
  return {
    ...credential,
    forge: credential.forge_id,
    secret: credential.secret_name,
    ...Object.fromEntries(Object.entries(credential.settings).map(([key, value]) => [settingField(credential.binding, key), value])),
  };
}

/** The patch an edit sends: the changed fields, and — when any setting changed — the binding's
 * whole settings as the form now holds them, which the hub stores in place of the old. */
export function credentialPatch(
  changed: Readonly<Record<string, string | number | boolean | null>>,
  values: ConfigFormValues,
  binding: string,
  bindings: readonly CredentialBindingView[],
): CredentialPatchRequest {
  const patch: Record<string, unknown> = {};
  let settingsChanged = false;
  for (const [key, value] of Object.entries(changed)) {
    if (key.startsWith('setting.')) settingsChanged = true;
    else patch[key] = value;
  }
  if (settingsChanged) patch['settings'] = formSettings(values, binding, bindings);
  return patch as CredentialPatchRequest;
}

export function credentialBadges(credential: CredentialSummary): readonly ConfigBadgeVm[] {
  return credential.retired ? [{ label: 'retired', tone: lifecycleTone(true) }] : [];
}

/** How many records name a credential, as the list shows it. */
export function referrerCountText(count: number): string {
  return count === 1 ? 'used by 1 record' : `used by ${count} records`;
}

/** The credential list's rows under `filter`. */
export function credentialRows(credentials: readonly CredentialListItem[], filter: LifecycleFilter): readonly ConfigRowVm[] {
  return filterByLifecycle(credentials, filter).map((credential) => ({
    key: credential.credential_id,
    title: credential.slug,
    sub: [
      ...(credential.name === credential.slug ? [] : [credential.name]),
      credential.forge_slug,
      credential.binding,
      referrerCountText(credential.referrer_count),
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
      { label: 'Forge', value: credential.forge_name },
      { label: 'Binding', value: credential.binding },
      { label: 'Secret', value: credential.secret_name },
      { label: 'Authority', value: credential.authority },
      ...Object.entries(credential.settings).map(([key, value]) => ({ label: key, value: settingText(value) })),
      { label: 'Created by', value: credential.created_by_name ?? credential.created_by },
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
export function credentialOptions(credentials: readonly CredentialSummary[]): readonly ConfigFieldOption[] {
  return credentials
    .filter((credential) => !credential.retired)
    .map((credential) => ({ value: credential.credential_id, label: `${credential.slug} · ${credential.binding}`, kind: credential.forge_id }));
}

/** `fields` with its `credential` select's choices filled in. */
export function credentialPickerFields<K extends string>(fields: readonly ConfigFieldDef<K>[], credentials: readonly CredentialSummary[]): readonly ConfigFieldDef<K>[] {
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
  readonly emptyText: string;
  readonly notEvaluated: string;
}

/** The tone each check outcome shows in: a refusal needs a person, an unsupported capability waits on a credential
 * change, and an unproven write is neither shown nor refused. */
export function checkOutcomeTone(outcome: OperationResultView['outcome']): Tone {
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
      unlisted(outcome);
      return 'idle';
  }
}

function checkOperationVm(result: OperationResultView): CheckOperationVm {
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
export function credentialCheckVm(check: CredentialCheckView): CredentialCheckVm {
  return {
    mint: check.mint && checkOperationVm(check.mint),
    identity: check.variant ? `${check.binding} · ${check.variant}` : check.binding,
    variantWarning: check.variant_contradicted ? 'The declared variant disagrees with the token itself.' : null,
    uses: check.uses.map((use) => ({
      key: `${use.kind}:${use.id}`,
      heading: `${use.kind.replace('_', ' ')} ${use.slug}`,
      reached: use.reached,
      operations: use.operations.map(checkOperationVm),
    })),
    emptyText: 'No enabled repository or work source uses this credential.',
    notEvaluated: check.not_evaluated,
  };
}

/** What a record's Check action checks: the credential, and the uses to check it against. */
export interface CredentialCheckTarget {
  readonly credentialId: string;
  readonly scope: CredentialCheckRequest;
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
export function shownCheckVm(answer: CredentialCheckView | undefined, current: boolean): CredentialCheckVm | null {
  return answer && current ? credentialCheckVm(answer) : null;
}

/** The failure line of a check call, when it failed for the target shown. */
export function checkFailure(failed: boolean, error: unknown, current: boolean): string | null {
  return failed && current ? errorMessage(error, 'Check failed.') : null;
}
