import { type Credential, type CredentialBinding, settingDrafts } from './credential.domain';
import { toCreateRequest, toDraft, toEditBaseline, toEditRequest } from './credential.translate';

const bindings: readonly CredentialBinding[] = [
  { name: 'token', forgeKind: 'github', settings: [{ key: 'ttl', type: { kind: 'positive-integer' } }, { key: 'variant', type: { kind: 'choice', choices: ['fine', 'classic'] } }] },
  { name: 'app', forgeKind: 'github', settings: [{ key: 'app-id', type: { kind: 'positive-integer' } }] },
];
const record: Credential = { id: 'id', slug: 'slug', name: 'name', forgeId: 'forge', forgeSlug: 'f', forgeName: 'Forge', binding: 'token', secretName: 'secret', settings: { ttl: 5, variant: 'fine', future: { x: 1 } }, authority: '', retired: false, revision: 3, createdBy: 'user', createdByName: null, createdAt: '' };
const text = (draft: ReturnType<typeof toDraft>, key: string, value: string) => ({ ...draft, settings: draft.settings.map((entry) => entry.binding === 'token' && entry.key === key ? { ...entry, text: value } : entry) });

describe('credential translation', () => {
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
});
