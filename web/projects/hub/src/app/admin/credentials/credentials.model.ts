import {
  type CredentialBindingView,
  type CredentialCreateRequest,
  type CredentialDocument,
  type CredentialListItem,
  type CredentialPatchRequest,
  type CredentialSummary,
  type ForgeSummary,
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

/** The binding choices: for each enabled forge, every binding of its kind, keyed by the forge, so
 * the select offers only the picked forge's bindings and a forge change clears the pick. */
export function bindingOptions(forges: readonly ForgeSummary[], bindings: readonly CredentialBindingView[]): readonly ConfigFieldOption[] {
  return forgeOptions(forges).flatMap((forge) =>
    bindings
      .filter((binding) => binding.forge_kind === forge.kind)
      .map((binding) => ({ value: binding.name, label: binding.name, kind: forge.value })),
  );
}

/** A credential's form fields. The forge and binding are fixed at create; each binding's settings
 * are selects of the choices the hub lists for it. */
export function credentialFields(forges: readonly ForgeSummary[], bindings: readonly CredentialBindingView[]): readonly ConfigFieldDef[] {
  return [
    { key: 'slug', label: 'Slug', kind: 'text', required: true },
    { key: 'name', label: 'Name', kind: 'text' },
    { key: 'forge', label: 'Forge', kind: 'select', required: true, createOnly: true, options: forgeOptions(forges) },
    { key: 'binding', label: 'Binding', kind: 'select', required: true, createOnly: true, filterBy: 'forge', options: bindingOptions(forges, bindings) },
    { key: 'secret', label: 'Secret', kind: 'text', required: true },
    ...bindings.flatMap((binding) =>
      binding.settings.map((setting): ConfigFieldDef => ({
        key: settingField(binding.name, setting.key),
        label: setting.key,
        kind: 'select',
        options: setting.choices.map((choice) => ({ value: choice, label: choice })),
        shownWhen: { key: 'binding', value: binding.name },
      })),
    ),
  ];
}

/** The settings the form holds for `binding` — each one left empty is omitted. */
function formSettings(values: Readonly<Record<string, string | boolean>>, binding: string, bindings: readonly CredentialBindingView[]): Record<string, string> {
  const settings: Record<string, string> = {};
  for (const setting of bindings.find((candidate) => candidate.name === binding)?.settings ?? []) {
    const value = values[settingField(binding, setting.key)];
    if (typeof value === 'string' && value.trim() !== '') settings[setting.key] = value.trim();
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
