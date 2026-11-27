import { computed, type Signal } from '@angular/core';
import { readRefusal } from 'fleet/core';
import { type SaveOutcome } from 'fleet/forms';
import { injectCreateCredentialMutation, injectEditCredentialMutation } from './credentials.mutations';
import type { CredentialBinding, CredentialDraft } from './credential.domain';
import { CREDENTIAL_REFUSALS, type CredentialEditBaseline, toCreateRequest, toEditRequest } from './credential.translate';

export interface CredentialService { readonly pending: Signal<boolean>; create(draft: CredentialDraft, bindings: readonly CredentialBinding[]): Promise<SaveOutcome<CredentialDraft>>; edit(before: CredentialEditBaseline, after: CredentialDraft): Promise<SaveOutcome<CredentialDraft>> }
export function injectCredentialService(): CredentialService {
  const createMutation = injectCreateCredentialMutation(); const editMutation = injectEditCredentialMutation();
  return {
    pending: computed(() => createMutation.isPending() || editMutation.isPending()),
    async create(draft, bindings) {
      const translated = toCreateRequest(draft, bindings);
      if (!translated.ok) return { ok: false, fields: {}, form: translated.form };
      try { await createMutation.mutateAsync(translated.request); return { ok: true }; } catch (error) { return readRefusal(error, CREDENTIAL_REFUSALS, 'Create failed.'); }
    },
    async edit(before, after) {
      const translated = toEditRequest(before, after);
      if (!translated.ok) return { ok: false, fields: {}, form: translated.form };
      try { await editMutation.mutateAsync({ credentialId: before.id, revision: before.revision, body: translated.request }); return { ok: true }; } catch (error) { return readRefusal(error, CREDENTIAL_REFUSALS); }
    },
  };
}
