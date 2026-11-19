import type { CredentialBindingView, CredentialCheckView, CredentialCreateRequest, CredentialDocument as WireDocument, CredentialListItem as WireListItem, CredentialPatchRequest, CredentialSummary, EditBaseline, ForgeBindingsView, RefusalMap } from 'fleet';
import { openLookup } from 'fleet';
import { type Credential, type CredentialBinding, type CredentialCheck, type CredentialDocument, type CredentialDraft, type CredentialForgeBindings, type CredentialListItem, type CredentialSettingKind, POSITIVE_INTEGER_MESSAGE, readPositiveInteger, settingDrafts } from './credential.domain';

export function fromCredentialSummary(wire: CredentialSummary): Credential {
  return { id: wire.credential_id, slug: wire.slug, name: wire.name, forgeId: wire.forge_id, forgeSlug: wire.forge_slug, forgeName: wire.forge_name, binding: wire.binding, secretName: wire.secret_name, settings: { ...wire.settings }, authority: wire.authority, retired: wire.retired, revision: wire.revision, createdBy: wire.created_by, createdByName: wire.created_by_name, createdAt: wire.created_at };
}
export function fromCredentialListItem(wire: WireListItem): CredentialListItem { return { ...fromCredentialSummary(wire), referrerCount: wire.referrer_count }; }
export function fromCredentialDocument(wire: WireDocument): CredentialDocument { return { ...fromCredentialSummary(wire), referrers: wire.referrers.map((ref) => ({ kind: ref.kind, key: ref.key, label: ref.label })) }; }
export function fromCredentialBinding(wire: CredentialBindingView): CredentialBinding {
  return { name: wire.name, forgeKind: wire.forge_kind, settings: wire.settings.map((setting) => {
    const type: CredentialSettingKind = setting.kind === 'choice' ? { kind: 'choice', choices: [...setting.choices] } : setting.kind === 'positive-integer' ? { kind: 'positive-integer' } : { kind: 'unknown', raw: setting.kind };
    return { key: setting.key, type };
  }) };
}
export function fromForgeBindings(wire: ForgeBindingsView): CredentialForgeBindings { return { forgeId: wire.forge_id, bindings: [...wire.bindings] }; }
const checkOutcome = openLookup<NonNullable<CredentialCheckView['mint']>['outcome'], import('./credential.domain').CredentialCheckOutcome>(
  { proven: 'proven', granted: 'granted', unproven: 'unproven', refused: 'refused', unsupported: 'unsupported' }, (unlisted) => ({ unlisted }),
);
const checkUseKind = openLookup<CredentialCheckView['uses'][number]['kind'], import('./credential.domain').CredentialUseKind>(
  { repository: 'repository', work_source: 'work_source' }, (unlisted) => ({ unlisted }),
);
function fromCheckOperation(wire: NonNullable<CredentialCheckView['mint']>): import('./credential.domain').CredentialCheckOperation {
  return { ...wire, outcome: checkOutcome(wire.outcome) };
}
export function fromCredentialCheck(wire: CredentialCheckView): CredentialCheck { return { binding: wire.binding, credential: wire.credential, grantedPermissions: wire.granted_permissions, mint: wire.mint && fromCheckOperation(wire.mint), notEvaluated: wire.not_evaluated, unreachedReason: wire.unreached_reason, uses: wire.uses.map((use) => ({ ...use, kind: checkUseKind(use.kind), operations: use.operations.map(fromCheckOperation) })), variant: wire.variant, variantContradicted: wire.variant_contradicted }; }
export function toDraft(record: Credential, bindings: readonly CredentialBinding[]): CredentialDraft {
  return { slug: record.slug, name: record.name, forge: record.forgeId, binding: record.binding, secret: record.secretName, settings: settingDrafts(bindings).map((entry) => {
    const value = record.settings[entry.key];
    return entry.binding === record.binding && (typeof value === 'string' || typeof value === 'number') ? { ...entry, text: String(value) } : entry;
  }) };
}
export interface CredentialEditBaseline extends EditBaseline<CredentialDraft> { readonly storedSettings: Readonly<Record<string, unknown>>; readonly bindings: readonly CredentialBinding[] }
export function toEditBaseline(record: Credential, bindings: readonly CredentialBinding[]): CredentialEditBaseline {
  const detached = structuredClone(bindings);
  return { id: record.id, revision: record.revision, draft: toDraft(record, detached), storedSettings: structuredClone(record.settings), bindings: detached };
}
export type CredentialRequest<R> = { readonly ok: true; readonly request: R } | { readonly ok: false; readonly form: string };
function settings(draft: CredentialDraft, stored: Readonly<Record<string, unknown>>, definitions: readonly CredentialBinding[], originalDraft?: CredentialDraft): CredentialRequest<Record<string, unknown>> {
  const binding = definitions.find((item) => item.name === draft.binding);
  if (!binding) return { ok: false, form: `Settings for binding ${draft.binding} are unavailable.` };
  const result = { ...stored };
  for (const definition of binding.settings) {
    const entry = draft.settings.find((item) => item.binding === binding.name && item.key === definition.key);
    if (!entry) continue;
    const originalEntry = originalDraft?.settings.find((item) => item.binding === binding.name && item.key === definition.key);
    if (originalEntry && entry.text === originalEntry.text) continue;
    const text = entry.text.trim();
    if (!text) {
      const original = stored[definition.key];
      if (original === undefined || typeof original === 'string' || typeof original === 'number') delete result[definition.key];
      continue;
    }
    if (definition.type.kind === 'positive-integer') {
      const number = readPositiveInteger(text);
      if (number === null) return { ok: false, form: `${definition.key}: ${POSITIVE_INTEGER_MESSAGE}` };
      result[definition.key] = number;
    } else result[definition.key] = text;
  }
  return { ok: true, request: result };
}
export function toCreateRequest(draft: CredentialDraft, bindings: readonly CredentialBinding[]): CredentialRequest<CredentialCreateRequest> {
  const result = settings(draft, {}, bindings);
  if (!result.ok) return result;
  const name = draft.name.trim();
  return { ok: true, request: { slug: draft.slug.trim(), ...(name ? { name } : {}), forge: draft.forge, binding: draft.binding, secret: draft.secret.trim(), ...(Object.keys(result.request).length ? { settings: result.request } : {}) } };
}
export function toEditRequest(before: CredentialEditBaseline, after: CredentialDraft): CredentialRequest<CredentialPatchRequest> {
  const body: CredentialPatchRequest = {};
  for (const key of ['slug', 'name', 'secret'] as const) { const text = after[key].trim(); if (text !== before.draft[key].trim()) body[key] = text; }
  if (before.bindings.some((binding) => binding.name === before.draft.binding)) {
    const next = settings({ ...after, binding: before.draft.binding }, before.storedSettings, before.bindings, before.draft);
    if (!next.ok) return next;
    if (JSON.stringify(before.storedSettings) !== JSON.stringify(next.request)) body.settings = next.request;
  }
  return { ok: true, request: body };
}
const FIELDS: Readonly<Record<string, keyof CredentialDraft>> = { slug: 'slug', name: 'name', forge: 'forge', binding: 'binding', secret: 'secret' };
export const CREDENTIAL_REFUSALS: RefusalMap<CredentialDraft> = { field: (path) => Object.hasOwn(FIELDS, path) ? FIELDS[path] : null, kinds: { 'urn:blizzard:problem:credential-slug-taken': 'slug' } };
