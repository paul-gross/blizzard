import { readWholeNumber } from 'fleet';

export type CredentialSettingKind = { readonly kind: 'choice'; readonly choices: readonly string[] } | { readonly kind: 'positive-integer' } | { readonly kind: 'unknown'; readonly raw: string };
export interface CredentialSettingDefinition { readonly key: string; readonly type: CredentialSettingKind }
export interface CredentialBinding { readonly name: string; readonly forgeKind: string; readonly settings: readonly CredentialSettingDefinition[] }
export interface CredentialForgeBindings { readonly forgeId: string; readonly bindings: readonly string[] }
export interface CredentialSettingDraft { readonly binding: string; readonly key: string; readonly type: CredentialSettingKind; readonly text: string }
export interface CredentialDraft { readonly slug: string; readonly name: string; readonly forge: string; readonly binding: string; readonly secret: string; readonly settings: readonly CredentialSettingDraft[] }
export const EMPTY_CREDENTIAL_DRAFT: CredentialDraft = { slug: '', name: '', forge: '', binding: '', secret: '', settings: [] };
export interface Credential {
  readonly id: string; readonly slug: string; readonly name: string; readonly forgeId: string; readonly forgeSlug: string;
  readonly forgeName: string; readonly binding: string; readonly secretName: string; readonly settings: Readonly<Record<string, unknown>>;
  readonly authority: string; readonly retired: boolean; readonly revision: number; readonly createdBy: string;
  readonly createdByName: string | null; readonly createdAt: string;
}
export interface CredentialListItem extends Credential { readonly referrerCount: number }
export interface CredentialReference { readonly kind: string; readonly key: string; readonly label: string }
export interface CredentialDocument extends Credential { readonly referrers: readonly CredentialReference[] }
export interface CredentialCheckScope { readonly repositories?: string[] | null; readonly work_sources?: string[] | null }
export interface CredentialCheckTarget { readonly credentialId: string; readonly scope: CredentialCheckScope }
export interface CredentialCheckOperation { readonly operation: string; readonly outcome: string; readonly detail: string | null; readonly diagnostic: { readonly status: number; readonly endpoint: string; readonly message: string } | null }
export interface CredentialCheckUse { readonly id: string; readonly kind: string; readonly slug: string; readonly reached: boolean; readonly operations: readonly CredentialCheckOperation[] }
export interface CredentialCheck { readonly binding: string; readonly credential: string; readonly grantedPermissions: Readonly<Record<string, string>> | null; readonly mint: CredentialCheckOperation | null; readonly notEvaluated: string; readonly unreachedReason: string | null; readonly uses: readonly CredentialCheckUse[]; readonly variant: string | null; readonly variantContradicted: boolean }
export const POSITIVE_INTEGER_MESSAGE = 'Enter a whole number from 1';
export function readPositiveInteger(text: string): number | null { const n = readWholeNumber(text); return n !== null && n >= 1 ? n : null; }
export function availableBindings(forge: string, eligibility: readonly CredentialForgeBindings[]): readonly string[] { return eligibility.find((item) => item.forgeId === forge)?.bindings ?? []; }
export function withForge(draft: CredentialDraft, forge: string, eligibility: readonly CredentialForgeBindings[]): CredentialDraft { return { ...draft, forge, binding: availableBindings(forge, eligibility).includes(draft.binding) ? draft.binding : '' }; }
export function settingDrafts(bindings: readonly CredentialBinding[]): readonly CredentialSettingDraft[] { return bindings.flatMap((binding) => binding.settings.map((setting) => ({ binding: binding.name, key: setting.key, type: setting.type, text: '' }))); }

/** A presentation row for the proposed credential edit. */
export interface CredentialChange { field: string; old: unknown; new: unknown; old_label: string | null; new_label: string | null }
