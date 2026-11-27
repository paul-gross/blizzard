import { type Credential, type CredentialBinding, settingDrafts } from './credential.domain';
import type { CredentialCheckView } from 'fleet/hub-api';
import { fromCredentialCheck, toCreateRequest, toDraft, toEditBaseline, toEditRequest } from './credential.translate';
import { credentialCheckVm } from './credentials.model';

const bindings: readonly CredentialBinding[] = [
  { name: 'token', forgeKind: 'github', settings: [{ key: 'ttl', type: { kind: 'positive-integer' } }, { key: 'variant', type: { kind: 'choice', choices: ['fine', 'classic'] } }] },
  { name: 'app', forgeKind: 'github', settings: [{ key: 'app-id', type: { kind: 'positive-integer' } }] },
];
const record: Credential = { id: 'id', slug: 'slug', name: 'name', forgeId: 'forge', forgeSlug: 'f', forgeName: 'Forge', binding: 'token', secretName: 'secret', settings: { ttl: 5, variant: 'fine', future: { x: 1 } }, authority: '', retired: false, revision: 3, createdBy: 'user', createdByName: null, createdAt: '' };
const text = (draft: ReturnType<typeof toDraft>, key: string, value: string) => ({ ...draft, settings: draft.settings.map((entry) => entry.binding === 'token' && entry.key === key ? { ...entry, text: value } : entry) });

describe('credential translation', () => {
  it('translates known and unforeseen check vocabularies, retaining raw values for display', () => {
    const operation = { operation: 'read', outcome: 'proven' as const, detail: null, diagnostic: null };
    const wire: CredentialCheckView = {
      binding: 'token', credential: 'cred', granted_permissions: null, mint: { ...operation, outcome: 'refused' },
      not_evaluated: '', unreached_reason: null, variant: null, variant_contradicted: false,
      uses: [
        { id: '1', kind: 'repository', slug: 'repo', reached: true, operations: ['proven', 'granted', 'unproven', 'refused', 'unsupported'].map((outcome) => ({ ...operation, outcome: outcome as typeof operation.outcome })) },
        { id: '2', kind: 'future-kind' as 'repository', slug: 'other', reached: true, operations: [{ ...operation, outcome: 'future-outcome' as 'proven' }] },
      ],
    };
    const check = fromCredentialCheck(wire);
    expect(check.mint?.outcome).toBe('refused');
    expect(check.uses[0].operations.map((op) => op.outcome)).toEqual(['proven', 'granted', 'unproven', 'refused', 'unsupported']);
    expect(check.uses[1].kind).toEqual({ unlisted: 'future-kind' });
    expect(check.uses[1].operations[0].outcome).toEqual({ unlisted: 'future-outcome' });
    const vm = credentialCheckVm(check);
    expect(vm.uses[1].heading).toBe('future-kind other');
    expect(vm.uses[1].operations[0]).toMatchObject({ outcome: 'future-outcome', tone: 'idle' });
  });
  it('sends only active settings and omits blank optional fields', () => {
    const draft = { ...toDraft(record, bindings), name: '', settings: settingDrafts(bindings).map((entry) => ({ ...entry, text: entry.binding === 'app' ? 'bad' : entry.key === 'ttl' ? '7' : '' })) };
    expect(toCreateRequest(draft, bindings)).toEqual({ ok: true, request: { slug: 'slug', forge: 'forge', binding: 'token', secret: 'secret', settings: { ttl: 7 } } });
  });
  it('rejects invalid active numbers without coercion', () => {
    for (const value of ['0', '1.2', '9007199254740992']) expect(toCreateRequest(text(toDraft(record, bindings), 'ttl', value), bindings).ok).toBe(false);
  });
  it('uses value equality, sparse patches and whole replacement preserving unknowns', () => {
    const baseline = toEditBaseline(record, bindings);
    expect(toEditRequest(baseline, text(baseline.draft, 'ttl', '05'))).toEqual({ ok: true, request: {} });
    expect(toEditRequest(baseline, text(baseline.draft, 'variant', ''))).toEqual({ ok: true, request: { settings: { ttl: 5, future: { x: 1 } } } });
    expect(toEditRequest(baseline, { ...baseline.draft, forge: 'other', binding: 'app', name: 'changed' })).toEqual({ ok: true, request: { name: 'changed' } });
  });
  it('retains settings if schema is unavailable', () => {
    const baseline = toEditBaseline(record, []);
    expect(toEditRequest(baseline, { ...baseline.draft, name: 'changed' })).toEqual({ ok: true, request: { name: 'changed' } });
  });
  it('sends an empty replacement when the last known setting is removed', () => {
    const baseline = toEditBaseline({ ...record, settings: { ttl: 5 } }, bindings);
    expect(toEditRequest(baseline, text(baseline.draft, 'ttl', ''))).toEqual({ ok: true, request: { settings: {} } });
  });
  it('allows an obsolete invalid stored number to be repaired or removed', () => {
    const baseline = toEditBaseline({ ...record, settings: { ttl: 0, future: { x: 1 } } }, bindings);
    expect(toEditRequest(baseline, text(baseline.draft, 'ttl', '8'))).toEqual({ ok: true, request: { settings: { ttl: 8, future: { x: 1 } } } });
    expect(toEditRequest(baseline, text(baseline.draft, 'ttl', ''))).toEqual({ ok: true, request: { settings: { future: { x: 1 } } } });
  });
  it('preserves an unchanged unknown-kind value while allowing edits and clearing', () => {
    const definitions: readonly CredentialBinding[] = [{ ...bindings[0], settings: [...bindings[0].settings, { key: 'future-kind', type: { kind: 'unknown', raw: 'future' } }] }];
    const baseline = toEditBaseline({ ...record, settings: { ttl: 0, variant: 'fine', 'future-kind': 12 } }, definitions);
    expect(toEditRequest(baseline, text(baseline.draft, 'variant', 'classic'))).toEqual({ ok: true, request: { settings: { ttl: 0, variant: 'classic', 'future-kind': 12 } } });
    expect(toEditRequest(baseline, text(baseline.draft, 'future-kind', '13'))).toEqual({ ok: true, request: { settings: { ttl: 0, variant: 'fine', 'future-kind': '13' } } });
    expect(toEditRequest(baseline, text(baseline.draft, 'future-kind', ''))).toEqual({ ok: true, request: { settings: { ttl: 0, variant: 'fine' } } });
  });
});
